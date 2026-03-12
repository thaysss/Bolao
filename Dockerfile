# Usa uma imagem Python slim para ocupar menos espaço
FROM python:3.10-slim

# Define o diretório de trabalho
WORKDIR /app

# Copia os requisitos e instala
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código fonte
COPY . .

# Expõe a porta que o FastAPI usa
EXPOSE 8000

# Comando para iniciar o servidor
# Comando final no seu Dockerfile
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]