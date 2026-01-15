FROM vllm/vllm-openai:latest as base
ENTRYPOINT []

RUN apt-get update && apt-get install -y git curl && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3 /usr/bin/python &&  pip install --no-cache-dir \
    git+https://github.com/huggingface/transformers \
    qwen-vl-utils==0.0.14 \
    torchcodec==0.9.1 \
    gradio==5.49.1 \
    transformers-stream-generator==0.0.5 \
    accelerate

# Patch vllm to fix ImportError with newer transformers
RUN sed -i 's/from transformers.configuration_utils import ALLOWED_LAYER_TYPES/ALLOWED_LAYER_TYPES = {"default", "linear", "dynamic", "su", "longrope", "llama3", "yarn"}/g' /usr/local/lib/python3.12/dist-packages/vllm/config/model.py && \
    sed -i 's/from transformers.configuration_utils import ALLOWED_LAYER_TYPES/ALLOWED_LAYER_TYPES = {"default", "linear", "dynamic", "su", "longrope", "llama3", "yarn"}/g' /usr/local/lib/python3.12/dist-packages/vllm/transformers_utils/config.py && \
    sed -i 's/from transformers.configuration_utils import ALLOWED_LAYER_TYPES/ALLOWED_LAYER_TYPES = {"default", "linear", "dynamic", "su", "longrope", "llama3", "yarn"}/g' /usr/local/lib/python3.12/dist-packages/vllm/model_executor/models/transformers/utils.py

ENTRYPOINT ["python3", "-m", "vllm.entrypoints.openai.api_server"]
CMD ["--model", "Qwen/Qwen3-VL-8B-Instruct-FP8", "--trust-remote-code", "--enforce-eager", "--max-model-len", "16384", "--gpu-memory-utilization", "0.94"]
