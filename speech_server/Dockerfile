# Alfred speech server for Jetson Orin (JetPack 6 / L4T r36.4, CUDA 12.6).
# The base image brings a CUDA build of PyTorch; the constraints file pins it so pip can never
# replace it with a CPU-only wheel from PyPI while installing the rest.
FROM dustynv/l4t-pytorch:r36.4.0

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# The base image points pip at extra NVIDIA indexes that stall resolution; use plain PyPI only.
ENV PIP_CONFIG_FILE=/dev/null PIP_INDEX_URL=https://pypi.org/simple PIP_TRUSTED_HOST= PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models/hf TORCH_HOME=/models/torch PYTHONUNBUFFERED=1

RUN python3 -c "import torch, torchaudio, numpy; print(f'torch=={torch.__version__}\ntorchaudio=={torchaudio.__version__}\nnumpy=={numpy.__version__}')" > /constraints.txt \
    && cat /constraints.txt

COPY requirements.txt /app/requirements.txt
RUN pip install -c /constraints.txt -r /app/requirements.txt \
    && python3 -m spacy download en_core_web_sm

COPY server.py /app/server.py
WORKDIR /app
EXPOSE 8770
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8770", "--no-server-header"]
