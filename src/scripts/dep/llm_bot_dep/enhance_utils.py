# python shell only support boto3 1.22.5 (1.28.68), according to https://docs.aws.amazon.com/glue/latest/dg/add-job-python.html#python-shell-limitations
import boto3
import json
import logging

# print the log to stdout
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# specify the profile to call the bedrock api
session = boto3.Session()
bedrock = session.client(
    service_name='bedrock',
    region_name='us-east-1',
    endpoint_url='https://bedrock-runtime.us-east-1.amazonaws.com'
)

en_prompt_template = """
Here is snippet of {solution}'s manual document within backticks
```
{page}
```
Please generate 10 questions and corresponding answers based on these document fragments, with the questions being as diverse as possible and containing details, following the rules below:
1. "{solution}" needs to be included in the Question continuously
2. The question part needs to start with "Question: "
3. The answer part needs to start with "Answer: "
4. All questions and answers need to be in Chinese
"""

zh_prompt_template = """
如下三个反括号中是{solution}的产品文档片段
```
{page}
```
请基于这些文档片段自动生成10个问题以及对应答案, 问题需要尽可能多样化并包含细节, 且遵循如下规则:
1. "{solution}"需要一直被包含在Question中
2. 问题部分需要以"Question: "开始
3. 答案部分需要以"Answer: "开始
4. 所有问题和答案需要为中文
"""

def EnhanceWithClaude(prompt: str, solution_title: str, page_content: str, zh: bool = True):
    """
    Enhance the given prompt using the Claude model by Anthropic. This function constructs a new prompt using the given solution title and page content,
    sends a request to the Claude model, and retrieves the model's response.

    Parameters:
    - prompt (str): The original prompt to be enhanced.
    - solution_title (str): The title of the solution to be included in the new prompt.
    - page_content (str): The content of the page to be included in the new prompt.
    - zh (bool): A flag indicating whether to use the Chinese or English prompt template. Default is True (Chinese).

    Returns:
    - str: The completion generated by the Claude model.

    Example:
    ```python
    prompt = "Do we have any solution offer to Stable Diffusion?"
    solution_title = "Stable Diffusion AWS Extensions"
    page_content = "Stable Diffusion AWS Extensions is a CSDC solution that..."
    enhanced_prompt = EnhanceWithClaude(prompt, solution_title, page_content)
    ```

    Note:
    - Claude v2 does not output Chinese characters in experiment, so Claude v1 is used here.
    """
    prompt_template = zh_prompt_template if zh else en_prompt_template
    prompt = prompt_template.format(solution=solution_title, page=page_content)
    prompt = "\n\nHuman:{}".format(prompt) + "\n\nAssistant:"
    # schema keep changing, refer to https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters.html#model-parameters-claude for latest schema
    body = json.dumps({
        "prompt": prompt,
        "temperature": 0.7,
        "top_p": 1,
        "top_k": 0,
        "max_tokens_to_sample": 500,
        "stop_sequences": ["\n\nHuman:"]
    })
    # note v2 is not output chinese characters
    modelId = "anthropic.claude-v1"
    accept = "*/*"
    contentType = "application/json"

    response = bedrock.invoke_model(
        body=body, modelId=modelId, accept=accept, contentType=contentType
    )
    response_body = json.loads(response.get("body").read())
    return response_body.get("completion")