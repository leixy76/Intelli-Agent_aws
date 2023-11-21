## LLM Bot

### Overall Architecture
![image](https://github.com/aws-samples/llm-bot/assets/23544182/acb918e3-3253-4cef-82bb-da65d78b1249)

### ETL Workflow
![image](https://github.com/aws-samples/llm-bot/assets/23544182/f35915ee-69ef-4f15-af83-e0df1d1249be)

### Quick Start

1. Prepare model assets by executing script per model folder
```bash
cd source/model/<cross/embedding/instruct>/model
./model.sh       
Make sure Python installed properly. Usage: ./model.sh -t TOKEN [-m MODEL_NAME] [-c COMMIT_HASH] [-s S3_BUCKET_NAME]
  -t TOKEN            Hugging Face token (required)
  -m MODEL_NAME       Model name (default: csdc-atl/buffer-cross-001)
  -c COMMIT_HASH      Commit hash (default: 46d270928463db49b317e5ea469a8ac8152f4a13)
  -s S3_BUCKET_NAME   S3 bucket name to upload the model (default: llm-rag)
./model.sh -t <Your Hugging Face Token> -s <Your S3 Bucket Name>

cd source/model/etl/code
sh model.sh ./Dockerfile <EtlImageName>
```
The ETL image will be pushed to your ECR repo with the image name you specified when executing the command sh model.sh ./Dockerfile <EtlImageName>


2. Deploy CDK template (add sudo if you are using Linux)
```bash
git clone --recursive
git submodule update --init

optional step to deploy AI Solution Kit Endpoints (OCR, Semantic Chunk Splitting, Chunk Summary):
cd submodule
npx projen build
npx cdk deploy

cd source/infrastructure
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws
npm install
npx cdk deploy --rollback false --parameters S3ModelAssets=<Your S3 Bucket Name> --parameters SubEmail=<Your email address> --parameters OpenSearchIndex=<Your OpenSearch Index Name> --parameters EtlImageName=<Your ETL model name>
```

**Deployment parameters**

| Parameter | Description |
|-|-|
| S3ModelAssets | Your bucket name to store models |
| SubEmail | Your email address to receive notifications |
| OpenSearchIndex | OpenSearch index name to store the knowledge, if the index is not existed, the solution will create one |
| EtlImageName | ETL image name, eg. etl-model, it is set when you executing source/model/etl/code/model.sh script |

You can update us-east-1 to any other available region according to your need. You will get output similar like below:
```
Outputs:
llm-bot-dev.APIEndpointAddress = https://xx.execute-api.us-east-1.amazonaws.com/v1/
llm-bot-dev.CrossModelEndpoint = cross-endpoint
llm-bot-dev.DocumentBucket = llm-bot-documents-xx-us-east-1
llm-bot-dev.EmbeddingModelEndpoint = embedding-endpoint
llm-bot-dev.GlueJobName = PythonShellJobxx
llm-bot-dev.InstructModelEndpoint = instruct-endpoint
llm-bot-dev.OpenSearchEndpoint = vpc-xx.us-east-1.es.amazonaws.com
llm-bot-dev.VPC = vpc-xx
Stack ARN:
arn:aws:cloudformation:us-east-1:xx:stack/llm-bot-dev/xx
```

3. Test the API connection

Use Postman/cURL to test the API connection, the API endpoint is the output of CloudFormation Stack with prefix 'embedding' or 'llm', the sample URL will be like "https://xxxx.execute-api.us-east-1.amazonaws.com/v1/embedding", the API request body is as follows:

**Offline process to pre-process file specified in S3 bucket and prefix, POST https://xxxx.execute-api.us-east-1.amazonaws.com/v1/etl**
```bash
BODY
{
    "s3Bucket": "<Your S3 bucket>",
    "s3Prefix": "<Your S3 prefix>",
    "offline": "true"
}
```
You should see output like this:
```bash
"Step Function triggered, Step Function ARN: arn:aws:states:us-east-1:xxxx:execution:xx-xxx:xx-xx-xx-xx-xx, Input Payload: {\"s3Bucket\": \"<Your S3 bucket>\", \"s3Prefix\": \"<Your S3 prefix>\", \"offline\": \"true\"}"
```

**Embedding uploaded file into AOS, POST https://xxxx.execute-api.us-east-1.amazonaws.com/v1/embedding, will be deprecate in the future**
```bash
BODY
{
  "document_prefix": "<Your S3 bucket prefix>",
  "aos_index": "chatbot-index"
}
```
You should see output like this:
```bash
{
  "created": xx.xx,
  "model": "embedding-endpoint"
}
```

**Then you can query embeddings in AOS, POST https://xxxx.execute-api.us-east-1.amazonaws.com/v1/embedding**, other operation including index, delete, query are also provided for debugging purpose.
```bash
BODY
{
  "aos_index": "chatbot-index",
  "operation": "match_all",
  "body": ""
}
```

You should see output like this:
```bash
{
  "took": 4,
  "timed_out": false,
  "_shards": {
    "total": 4,
    "successful": 4,
    "skipped": 0,
    "failed": 0
  },
  "hits": {
    "total": {
      "value": 256,
      "relation": "eq"
    },
    "max_score": 1.0,
    "hits": [
      {
        "_index": "chatbot-index",
        "_id": "035e8439-c683-4278-97f3-151f8cd4cdb6",
        "_score": 1.0,
        "_source": {
          "vector_field": [
            -0.03106689453125,
            -0.00798797607421875,
            ...
          ],
          "text": "## 1 Introduction\n\nDeep generative models of all kinds have recently exhibited high quality samples in a wide variety of data modalities. Generative adversarial networks (GANs), autoregressive models, flows, and variational autoencoders (VAEs) have synthesized striking image and audio samples [14; 27; 3; 58; 38; 25; 10; 32; 44; 57; 26; 33; 45], and there have been remarkable advances in energy-based modeling and score matching that have produced images comparable to those of GANs [11; 55].",
          "metadata": {
            "content_type": "paragraph",
            "heading_hierarchy": {
              "1 Introduction": {}
            },
            "figure_list": [],
            "chunk_id": "$2",
            "file_path": "Denoising Diffusion Probabilistic Models.pdf",
            "keywords": [],
            "summary": ""
          }
        }
      },
      ...
    ]
  }
}
```

**Delete initial index in AOS, POST https://xxxx.execute-api.us-east-1.amazonaws.com/v1/embedding for debugging purpose**
```bash
{
  "aos_index": "chatbot-index",
  "operation": "delete",
  "body": ""
}
```

**Create initial index in AOS, POST https://xxxx.execute-api.us-east-1.amazonaws.com/v1/embedding for debugging purpose**
```bash
{
  "aos_index": "chatbot-index",
  "operation": "create",
  "body": {
    "settings": {
      "index": {
        "number_of_shards": 2,
        "number_of_replicas": 1
      }
    },
    "mappings": {
      "properties": {
        "vector_field": {
            "type": "knn_vector",
            "dimension": 1024
        }
      }
    }
  }
}
```

**invoke LLM with context, POST https://xxxx.execute-api.us-east-1.amazonaws.com/v1/llm**
```bash
BODY
{
  "model": "knowledge_qa",
  "messages": [
    {
      "role": "user",
      "content": "给我介绍一下什么是data transfer hub方案？"
    }
  ],
  "temperature": 0.7
}
```
You should see output like this:
```bash
{
  "id": "user_1693493252",
  "object": "chat.completion",
  "created": 1693493252,
  "model": "knowledge_qa",
  "usage": {
    "prompt_tokens": 13,
    "completion_tokens": 7,
    "total_tokens": 20
  },
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "数据传输解决方案（Data Transfer Hub）是一种用于安全、可扩展和可追踪地将数据从不同云服务提供商的对象存储服务（如阿里云 OSS、腾讯 COS、七牛 Kodo等）传输到Amazon S3和Amazon ECR的方案。它提供了一个直观的用户界面，允许客户在界面上创建和管理数据传输任务。通过数据传输解决方案，客户可以实现将数据从其他云服务提供商的对象存储服务传输到Amazon S3，以及在Amazon ECR之间传输容器镜像。该方案采用无服务器架构，按需使用并随用随付。有关更多信息，请参阅实施指南的“成本”部分。",
        "knowledge_sources": [
          "/tmp/tmptezz8py3/csdc/dth.txt"
        ]
      },
      "finish_reason": "stop",
      "index": 0
    }
  ]
}
```

4. Launch dashboard to check and debug the ETL & QA process

```bash
cd /source/panel
pip install -r requirements.txt
mv .env_sample .env
# fill .env content accordingly with cdk output
streamlit run app.py --server.runOnSave true
```

5. [Optional] Upload embedding file to S3 bucket created in the previous step, the format is like below:
```bash
aws s3 cp dth.txt s3://llm-bot-documents-<your account id>-<region>/<your S3 bucket prefix>/
```
Now the object created event will trigger the Step function to execute Glue job for online processing.

## Other Sample
Try [Bedrock tuturial](https://github.com/aws-samples/llm-bot/blob/main/sample/bedrock-tuturial.ipynb) quick get though the bedrock model & langchain.

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This project is licensed under the Apache-2.0 License.

