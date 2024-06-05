import json
from textwrap import dedent
from typing import TypedDict,Any,Annotated
import validators
from langgraph.graph import StateGraph,END
from common_utils.lambda_invoke_utils import invoke_lambda,node_monitor_wrapper
from common_utils.python_utils import update_nest_dict,add_messages
from common_utils.constant import (
    LLMTaskType
)
import pandas as pd 

from functions.tools import get_tool_by_name,Tool
from functions.tool_execute_result_format import format_tool_execute_result
from functions.tool_calling_parse import parse_tool_calling as _parse_tool_calling

from lambda_main.main_utils.parse_config import parse_retail_entry_config
from common_utils.lambda_invoke_utils import send_trace,is_running_local
from common_utils.exceptions import ToolNotExistError,ToolParameterNotExistError
from common_utils.logger_utils import get_logger
from common_utils.serialization_utils import JSONEncoder
from common_utils.s3_utils import download_dir_from_s3

logger = get_logger('retail_entry')

def get_url_goods_dict(data_file_path)->dict:
    url_goods = {} 
    goods_data = pd.read_excel(data_file_path, "商品信息登记").to_dict(orient='records')
    # get row
    for datum in goods_data:
        goods_id = datum["商品ID"]
        goods_info = datum["卖点（含材质属性）"]
        goods_url = datum["商品链接"]
        url_goods[goods_url] = {
            "goods_info": goods_info,
            "goods_url": goods_url,
            "goods_id": goods_id
            } 
    return url_goods

download_dir_from_s3("aws-chatbot-knowledge-base-test", "retail", "lambda_main")
url_goods_dict = get_url_goods_dict("lambda_main/retail/detail/TB0327.xlsx")

class ChatbotState(TypedDict):
    chatbot_config: dict # chatbot config
    query: str 
    ws_connection_id: str 
    stream: bool 
    query_rewrite: str = None  # query rewrite ret
    intent_type: str = None # intent
    intention_fewshot_examples: list
    trace_infos: Annotated[list[str],add_messages]
    message_id: str = None
    chat_history: Annotated[list[dict],add_messages]
    agent_chat_history: Annotated[list[dict],add_messages]
    current_function_calls: list[str]
    current_tool_execute_res: dict
    debug_infos: Annotated[dict,update_nest_dict]
    answer: Any  # final answer
    current_monitor_infos: str 
    extra_response: Annotated[dict,update_nest_dict]
    contexts: str = None
    current_intent_tools: list #
    current_agent_intent_type: str = None
    current_tool_calls:list 
    current_agent_tools_def: list[dict]
    current_agent_model_id: str
    parse_tool_calling_ok: bool
    query_rule_classification: str
    

####################
# nodes in lambdas #
####################

@node_monitor_wrapper
def query_preprocess_lambda(state: ChatbotState):
    output:str = invoke_lambda(
        event_body=state,
        lambda_name="Online_Query_Preprocess",
        lambda_module_path="lambda_query_preprocess.query_preprocess",
        handler_name="lambda_handler"
    )
    state['extra_response']['query_rewrite'] = output
    send_trace(f"**query_rewrite:** \n{output}")
    return {
            "query_rewrite":output,
            "current_monitor_infos":f"query_rewrite: {output}"
        }

@node_monitor_wrapper
def intention_detection_lambda(state: ChatbotState):
    intention_fewshot_examples = invoke_lambda(
        lambda_module_path='lambda_intention_detection.intention',
        lambda_name="Online_Intention_Detection",
        handler_name="lambda_handler",
        event_body=state 
    )
    state['extra_response']['intention_fewshot_examples'] = intention_fewshot_examples

    # send trace
    send_trace(f"intention retrieved:\n{json.dumps(intention_fewshot_examples,ensure_ascii=False,indent=2)}")
    current_intent_tools:list[str] = list(set([e['intent'] for e in intention_fewshot_examples]))
    return {
        "intention_fewshot_examples": intention_fewshot_examples,
        "current_intent_tools": current_intent_tools,
        "intent_type":"other"
        }


@node_monitor_wrapper
def agent_lambda(state: ChatbotState):
    output:dict = invoke_lambda(
        event_body={**state,"chat_history":state['agent_chat_history']},
        lambda_name="Online_Agent",
        lambda_module_path="lambda_agent.agent",
        handler_name="lambda_handler"
    )
    current_function_calls = output['function_calls']
    content = output['content']
    current_agent_tools_def = output['current_agent_tools_def']
    current_agent_model_id = output['current_agent_model_id']
    send_trace(f"**current_function_calls:** \n{current_function_calls},\n**model_id:** \n{current_agent_model_id}\n**ai content:** \n{content}")
    return {
        "current_agent_model_id": current_agent_model_id,
        "current_function_calls": current_function_calls,
        "current_agent_tools_def": current_agent_tools_def,
        "agent_chat_history": [{
                    "role": "ai",
                    "content": content
                }]
    }


@node_monitor_wrapper
def parse_tool_calling(state: ChatbotState):
    """executor lambda
    Args:
        state (NestUpdateState): _description_

    Returns:
        _type_: _description_
    """
    # parse tool_calls:
    try:
        tool_calls = _parse_tool_calling(
            model_id = state['current_agent_model_id'],
            function_calls = state['current_function_calls'],
            tools=state['current_agent_tools_def'],
        )
        send_trace(f"**tool_calls parsed:** \n{tool_calls}")
        if tool_calls:
            state["extra_response"]['current_agent_intent_type'] = tool_calls[0]['name']
        else:
            return {
                "parse_tool_calling_ok": False,
                "agent_chat_history":[{
                    "role": "user",
                    "content": "当前没有解析到tool,请检查tool调用的格式是否正确，并重新输出某个tool的调用。注意调用tool的时候要加上<function_calls></function_calls>。如果你认为当前不需要调用其他工具，请直接调用“give_final_response”工具进行返回。"
                }]
            }

        return {
            "parse_tool_calling_ok": True,
            "current_tool_calls": tool_calls,
        }
    except (ToolNotExistError,ToolParameterNotExistError) as e:
        send_trace(f"**tool_calls parse failed:** \n{str(e)}")
        return {
        "parse_tool_calling_ok": False,
        "agent_chat_history":[{
            "role": "user",
            "content": format_tool_execute_result(
                model_id = state['current_agent_model_id'],
                tool_output={
                    "code": 1,
                    "result": e.to_agent(),
                    "tool_name": e.tool_name
                }
            )
        }]
        }

@node_monitor_wrapper
def tool_execute_lambda(state: ChatbotState):
    """executor lambda
    Args:
        state (NestUpdateState): _description_

    Returns:
        _type_: _description_
    """
    tool_calls = state['current_tool_calls']
    assert len(tool_calls) == 1, tool_calls
    tool_call_results = []
    for tool_call in tool_calls:
        tool_name = tool_call["name"]
        tool_kwargs = tool_call['kwargs']
        # call tool
        output = invoke_lambda(
            event_body = {
                "tool_name":tool_name,
                "state":state,
                "kwargs":tool_kwargs
                },
            lambda_name="Online_Tool_Execute",
            lambda_module_path="functions.lambda_tool",
            handler_name="lambda_handler"   
        )
        tool_call_results.append({
            "name": tool_name,
            "output": output,
            "kwargs": tool_call['kwargs'],
            "model_id": tool_call['model_id']
        })
    
    # convert tool calling as chat history
    tool_call_result_strs = []
    for tool_call_result in tool_call_results:
        tool_exe_output = tool_call_result['output']
        tool_exe_output['tool_name'] = tool_call_result['name']
        ret:str = format_tool_execute_result(
            tool_call_result["model_id"],
            tool_exe_output
        )
        tool_call_result_strs.append(ret)
    
    ret = "\n".join(tool_call_result_strs)
    return {
        "current_monitor_infos": ret,
        "agent_chat_history":[{
            "role": "user",
            "content": ret
    }]}



@node_monitor_wrapper
def rag_daily_reception_retriever_lambda(state: ChatbotState):
    # call retriever
    retriever_params = state["chatbot_config"]["rag_daily_reception_config"]['retriever_config']
    retriever_params["query"] = state["query"]
    output:str = invoke_lambda(
        event_body=retriever_params,
        lambda_name="Online_Function_Retriever",
        lambda_module_path="functions.lambda_retriever.retriever",
        handler_name="lambda_handler"
    )
    contexts = [doc['page_content'] for doc in output['result']['docs']]
    context = "\n".join(contexts)
    send_trace(f'**rag_goods_exchange_retriever** {context}')
    return {"contexts": contexts}

@node_monitor_wrapper
def rag_daily_reception_llm_lambda(state:ChatbotState):
    context = ("="*50).join(state['contexts'])
    prompt = dedent(f"""你是安踏的客服助理，正在帮用户解答问题，客户提出的问题大多是属于日常接待类别，你需要按照下面的guidelines进行回复:
                    <guidelines>
                      - 回复内容需要展现出礼貌。
                    </guidelines>
                    下面列举了一些具体的场景下的回复，你可以结合用户的问题进行参考回答:
                    <context>
                    {context}
                    </context>
                    下面是用户的回复: {state['query']}
""")
    output:str = invoke_lambda(
        lambda_name='Online_LLM_Generate',
        lambda_module_path="lambda_llm_generate.llm_generate",
        handler_name='lambda_handler',
        event_body={
            "llm_config": {**state['chatbot_config']['rag_daily_reception_config']['llm_config'], "intent_type": LLMTaskType.CHAT},
            "llm_input": { "query": prompt, "chat_history": state['chat_history']}
            }
        )
    return {"answer": output}

@node_monitor_wrapper
def rag_goods_exchange_retriever_lambda(state: ChatbotState):
    # call retriever
    retriever_params = state["chatbot_config"]["rag_goods_exchange_config"]['retriever_config']
    retriever_params["query"] = state["query"]
    output:str = invoke_lambda(
        event_body=retriever_params,
        lambda_name="Online_Function_Retriever",
        lambda_module_path="functions.lambda_retriever.retriever",
        handler_name="lambda_handler"
    )
    contexts = [doc['page_content'] for doc in output['result']['docs']]

    context = "\n".join(contexts)
    send_trace(f'**rag_goods_exchange_retriever** {context}')
    return {"contexts": contexts}


@node_monitor_wrapper
def rag_goods_exchange_llm_lambda(state:ChatbotState):
    context = ("="*50).join(state['contexts'])
    prompt = dedent(f"""你是安踏的客服助理，正在帮用户解答问题，客户提出的问题大多是属于商品退换货范畴，你需要按照下面的guidelines进行回复:
                    <guidelines>
                      - 回复内容需要展现出礼貌。
                    </guidelines>
                    下面列举了一些具体的场景下的回复，你可以结合用户的问题进行参考回答:
                    <context>
                    {context}
                    </context>
                    下面是用户的回复: {state['query']}
""")
    
    output:str = invoke_lambda(
        lambda_name='Online_LLM_Generate',
        lambda_module_path="lambda_llm_generate.llm_generate",
        handler_name='lambda_handler',
        event_body={
            "llm_config": {**state['chatbot_config']['rag_goods_exchange_config']['llm_config'], "intent_type": LLMTaskType.CHAT},
            "llm_input": { "query": prompt, "chat_history": state['chat_history']}
            }
        )
    return {"answer": output}

@node_monitor_wrapper
def rag_product_aftersales_retriever_lambda(state: ChatbotState):
    # call retriever
    retriever_params = state["chatbot_config"]["rag_product_aftersales_config"]["retriever_config"]
    retriever_params["query"] = state["query"]
    output:str = invoke_lambda(
        event_body=retriever_params,
        lambda_name="Online_Function_Retriever",
        lambda_module_path="functions.lambda_retriever.retriever",
        handler_name="lambda_handler"
    )
    contexts = [doc['page_content'] for doc in output['result']['docs']]

    context = "\n".join(contexts)
    send_trace(f'**rag_product_aftersales_retriever** {context}')
    return {"contexts": contexts}

@node_monitor_wrapper
def rag_product_aftersales_llm_lambda(state:ChatbotState):
    context = ("="*50).join(state['contexts'])
    prompt = dedent(f"""你是安踏的客服助理，正在帮用户解答问题，客户提出的问题大多是属于商品的商品质量和物流规则，可能包括你需要按照下面的guidelines进行回复:
                    <guidelines>
                      - 回复内容需要展现出礼貌。
                      - 回答要简洁。
                    </guidelines>
                    下面列举了一些具体的场景下的回复，你可以结合用户的问题进行参考回答:
                    <context>
                    {context}
                    </context>
                    下面是用户的回复: {state['query']}
""")
    output:str = invoke_lambda(
        lambda_name='Online_LLM_Generate',
        lambda_module_path="lambda_llm_generate.llm_generate",
        handler_name='lambda_handler',
        event_body={
            "llm_config": {**state['chatbot_config']['rag_product_aftersales_config']['llm_config'], "intent_type": LLMTaskType.CHAT},
            "llm_input": { "query": prompt, "chat_history": state['chat_history']}
            }
        )
    return {"answer": output}

@node_monitor_wrapper
def rag_customer_complain_retriever_lambda(state: ChatbotState):
    # call retriever
    retriever_params = state["chatbot_config"]["rag_customer_complain_config"]["retriever_config"]
    retriever_params["query"] = state["query"]
    output:str = invoke_lambda(
        event_body=retriever_params,
        lambda_name="Online_Function_Retriever",
        lambda_module_path="functions.lambda_retriever.retriever",
        handler_name="lambda_handler"
    )
    contexts = [doc['page_content'] for doc in output['result']['docs']]

    context = "\n".join(contexts)
    send_trace(f'**rag_customer_complain_retriever** {context}')
    return {"contexts": contexts}

@node_monitor_wrapper
def rag_customer_complain_llm_lambda(state:ChatbotState):
    context = ("="*50).join(state['contexts'])
    prompt = dedent(f"""你是安踏的客服助理，正在处理有关于客户抱怨的问题，这些问题有关于商品质量等方面，需要你按照下面的guidelines进行回复:
                    <guidelines>
                      - 回复内容需要展现出礼貌。
                      - 尽量安抚客户的情绪。
                      - 回答要简洁。
                    </guidelines>
                    下面列举了一些具体的场景下的回复，你可以结合用户的问题进行参考回答:
                    <context>
                    {context}
                    </context>
                    下面是用户的回复: {state['query']}
""")
    output:str = invoke_lambda(
        lambda_name='Online_LLM_Generate',
        lambda_module_path="lambda_llm_generate.llm_generate",
        handler_name='lambda_handler',
        event_body={
            "llm_config": {**state['chatbot_config']['rag_customer_complain_config']['llm_config'], "intent_type": LLMTaskType.CHAT},
            "llm_input": { "query": prompt, "chat_history": state['chat_history']}
            }
        )
    return {"answer": output}

@node_monitor_wrapper
def rag_promotion_retriever_lambda(state: ChatbotState):
    # call retriever
    retriever_params = state["chatbot_config"]["rag_promotion_config"]["retriever_config"]
    retriever_params["query"] = state["query"]
    output:str = invoke_lambda(
        event_body=retriever_params,
        lambda_name="Online_Function_Retriever",
        lambda_module_path="functions.lambda_retriever.retriever",
        handler_name="lambda_handler"
    )
    contexts = [doc['page_content'] for doc in output['result']['docs']]

    context = "\n".join(contexts)
    send_trace(f'**rag_promotion_retriever** {context}')
    return {"contexts": contexts}

@node_monitor_wrapper
def rag_promotion_llm_lambda(state:ChatbotState):
    context = ("="*50).join(state['contexts'])
    prompt = dedent(f"""你是安踏的客服助理，正在处理客户有关于商品促销的问题，这些问题有关于积分，奖品，奖励等方面，需要你按照下面的guidelines进行回复:
                    <guidelines>
                      - 回复内容需要展现出礼貌。
                      - 回答要简洁。
                    </guidelines>
                    下面列举了一些具体的场景下的回复，你可以结合用户的问题进行参考回答:
                    <context>
                    {context}
                    </context>
                    下面是用户的回复: {state['query']}
""")
    output:str = invoke_lambda(
        lambda_name='Online_LLM_Generate',
        lambda_module_path="lambda_llm_generate.llm_generate",
        handler_name='lambda_handler',
        event_body={
            "llm_config": {**state['chatbot_config']['rag_promotion_config']['llm_config'], "intent_type": LLMTaskType.CHAT},
            "llm_input": { "query": prompt, "chat_history": state['chat_history']}
            }
        )
    return {"answer": output}



def transfer_reply(state:ChatbotState):
    return {"answer": "立即为您转人工客服，请稍后"}


def give_rhetorical_question(state:ChatbotState):
    recent_tool_calling:list[dict] = state['current_tool_calls'][0]
    return {"answer": recent_tool_calling['kwargs']['question']}


def give_final_response(state:ChatbotState):
    recent_tool_calling:list[dict] = state['current_tool_calls'][0]
    return {"answer": recent_tool_calling['kwargs']['response']}

def rule_url_reply(state:ChatbotState):
    if state['query'].endswith(('.jpg','.png')):
        return {"answer": "好的，收到图片。"}
    # product information
    if state['query'] in url_goods_dict:
        return {"answer":url_goods_dict[state['query']]['goods_info']}
    
    return {"answer":"您好"}

def rule_number_reply(state:ChatbotState):
    return {"answer":"收到订单信息"}


################
# define edges #
################

def query_route(state:dict):
    # check if rule reply
    query = state['query']
    if validators.url(query):
        return "url"
    if query.isnumeric() and len(query)>=8:
        return "number"
    else:
        return "continue"

def intent_route(state:dict):
    return state['intent_type']

def agent_route(state:dict):
    parse_tool_calling_ok = state['parse_tool_calling_ok']
    if not parse_tool_calling_ok:
        return 'invalid tool calling'
    
    recent_tool_calls:list[dict] = state['current_tool_calls']

    # if not recent_tool_calls:
    #     return "no tool"
    
    recent_tool_call = recent_tool_calls[0]

    recent_tool_name = recent_tool_call['name']

    if recent_tool_name in ['comfort', 'transfer']:
        return recent_tool_name
    
    if recent_tool_call['name'] == "give_rhetorical_question":
        return "rhetorical question"
    
    if recent_tool_call['name'] == "goods_exchange":
        return "goods exchange"
    
    if recent_tool_call['name'] == "daily_reception":
        return "daily reception"

    if recent_tool_call['name'] == "rule_response":
        return "rule response"

    if recent_tool_call['name'] == 'product_logistics':
        return "product aftersales"

    if recent_tool_call['name'] == 'product_quality':
        return "product aftersales"

    if recent_tool_call['name'] == 'customer_complain':
        return "customer complain"

    if recent_tool_call['name'] == 'promotion':
        return "promotion"
    
    if recent_tool_call['name'] == "give_final_response":
        return "give final response"

    return "continue"
     
#############################
# define whole online graph #
#############################

def build_graph():
    workflow = StateGraph(ChatbotState)
    # add all nodes
    workflow.add_node("query_preprocess_lambda", query_preprocess_lambda)
    workflow.add_node("intention_detection_lambda", intention_detection_lambda)
    workflow.add_node("agent_lambda", agent_lambda)
    workflow.add_node("tool_execute_lambda", tool_execute_lambda)
    workflow.add_node("transfer_reply", transfer_reply)
    workflow.add_node("give_rhetorical_question",give_rhetorical_question)
    workflow.add_node("give_final_response",give_final_response)
    # workflow.add_node("give_response_wo_tool",give_response_without_any_tool)
    workflow.add_node("parse_tool_calling",parse_tool_calling)
    # 
    workflow.add_node("rag_daily_reception_retriever",rag_daily_reception_retriever_lambda)
    workflow.add_node("rag_daily_reception_llm",rag_daily_reception_llm_lambda)
    workflow.add_node("rag_goods_exchange_retriever",rag_goods_exchange_retriever_lambda)
    workflow.add_node("rag_goods_exchange_llm",rag_goods_exchange_llm_lambda)
    workflow.add_node("rag_product_aftersales_retriever",rag_product_aftersales_retriever_lambda)
    workflow.add_node("rag_product_aftersales_llm",rag_product_aftersales_llm_lambda)
    workflow.add_node("rag_customer_complain_retriever",rag_customer_complain_retriever_lambda)
    workflow.add_node("rag_customer_complain_llm",rag_customer_complain_llm_lambda)
    workflow.add_node("rule_url_reply",rule_url_reply)
    workflow.add_node("rule_number_reply",rule_number_reply)
    workflow.add_node("rag_promotion_retriever",rag_promotion_retriever_lambda)
    workflow.add_node("rag_promotion_llm",rag_promotion_llm_lambda)

    # add all edges
    workflow.set_entry_point("query_preprocess_lambda")
    # workflow.add_edge("query_preprocess_lambda","intention_detection_lambda")
    workflow.add_edge("intention_detection_lambda","agent_lambda")
    workflow.add_edge("tool_execute_lambda","agent_lambda")
    workflow.add_edge("agent_lambda",'parse_tool_calling')
    workflow.add_edge("rag_daily_reception_retriever","rag_daily_reception_llm")
    workflow.add_edge('rag_goods_exchange_retriever',"rag_goods_exchange_llm")
    workflow.add_edge('rag_product_aftersales_retriever',"rag_product_aftersales_llm")
    workflow.add_edge('rag_customer_complain_retriever',"rag_customer_complain_llm")
    workflow.add_edge('rag_promotion_retriever',"rag_promotion_llm")

    # end
    workflow.add_edge("transfer_reply",END)
    workflow.add_edge("give_rhetorical_question",END)
    # workflow.add_edge("give_response_wo_tool",END)
    workflow.add_edge("rag_daily_reception_llm",END)
    workflow.add_edge("rag_goods_exchange_llm",END)
    workflow.add_edge("rag_product_aftersales_llm",END)
    workflow.add_edge("rag_customer_complain_llm",END)
    workflow.add_edge('rule_url_reply',END)
    workflow.add_edge('rule_number_reply',END)
    workflow.add_edge("rag_promotion_llm",END)
    workflow.add_edge("give_final_response",END)

    # temporal add edges for ending logic
    # add conditional edges

    workflow.add_conditional_edges(
        "query_preprocess_lambda",
        query_route,
        {
           "url":  "rule_url_reply",
           "number": "rule_number_reply",
           "continue": "intention_detection_lambda"
        }
    )

    workflow.add_conditional_edges(
        "parse_tool_calling",
        agent_route,
        {
            "invalid tool calling": "agent_lambda",
            "rhetorical question": "give_rhetorical_question",
            "transfer": "transfer_reply",
            "goods exchange": "rag_goods_exchange_retriever",
            "daily reception": "rag_daily_reception_retriever",
            "product aftersales": "rag_product_aftersales_retriever",
            "customer complain": "rag_customer_complain_retriever",
            "promotion": "rag_promotion_retriever",
            "give final response": "give_final_response",
            "continue":"tool_execute_lambda"
        }
    )
    app = workflow.compile()
    return app

app = None 

def retail_entry(event_body):
    """
    Entry point for the Lambda function.
    :param event_body: The event body for lambda function.
    return: answer(str)
    """
    global app 
    if app is None:
        app = build_graph()

    # debuging
    # TODO only write when run local
    if is_running_local():
        with open('retail_entry_workflow.png','wb') as f:
            f.write(app.get_graph().draw_png())
    
    ################################################################################
    # prepare inputs and invoke graph
    event_body['chatbot_config'] = parse_retail_entry_config(event_body['chatbot_config'])
    logger.info(f'event_body:\n{json.dumps(event_body,ensure_ascii=False,indent=2,cls=JSONEncoder)}')
    chatbot_config = event_body['chatbot_config']
    query = event_body['query']
    use_history = chatbot_config['use_history']
    chat_history = event_body['chat_history'] if use_history else []
    stream = event_body['stream']
    message_id = event_body['custom_message_id']
    ws_connection_id = event_body['ws_connection_id']

    # invoke graph and get results
    response = app.invoke({
        "stream": stream,
        "chatbot_config": chatbot_config,
        "query": query,
        "trace_infos": [],
        "message_id": message_id,
        "chat_history": chat_history,
        "agent_chat_history": chat_history + [{"role":"user","content":query}],
        "ws_connection_id": ws_connection_id,
        "debug_infos": {},
        "extra_response": {},
    })

    return {"answer":response['answer'],**response["extra_response"]}

main_chain_entry = retail_entry