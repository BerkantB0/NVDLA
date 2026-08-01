FROM python:3.10-slim@sha256:c1e4e6c01eb489c422288b2de34b0761ca316f7a2d98e2c33f47659a73ed108a

COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir --disable-pip-version-check \
        -r /tmp/requirements.txt \
    && python -m pip install --no-cache-dir --disable-pip-version-check \
        --no-deps caffe2onnx==2.0.1

WORKDIR /work
