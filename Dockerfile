FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py md_to_adf.py ./

ENV MCP_PORT=8766
EXPOSE 8766

CMD ["python", "server.py"]
