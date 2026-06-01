FROM pytorch/pytorch:2.8.0-cuda12.8-cudnn9-runtime

WORKDIR /voc-studio

# Install system dependencies for audio processing
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY app/requirements.txt /voc-studio/app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt && \
    pip install --no-cache-dir qwen-tts==0.1.1

# Copy application code
COPY app/ /voc-studio/app/
COPY default_prompts.txt review_prompts.txt /voc-studio/
COPY builtin_lora/ /voc-studio/builtin_lora/

# Create directories for runtime data
RUN mkdir -p /voc-studio/scripts \
    /voc-studio/designed_voices \
    /voc-studio/clone_voices \
    /voc-studio/lora_models \
    /voc-studio/lora_datasets \
    /voc-studio/dataset_builder \
    /voc-studio/app/uploads

# Bind to 0.0.0.0 inside the container
ENV VOC_STUDIO_HOST=0.0.0.0
EXPOSE 4200

CMD ["python", "app/app.py"]
