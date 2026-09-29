# The Daily Herald demo. Deliberately defective by design -- see DEFECTS.md.
# Pure standard library, so no dependencies are installed.
FROM python:3.12-alpine

WORKDIR /app

COPY server.py ./server.py
COPY index.html ./index.html
COPY css ./css
COPY js ./js
COPY third-party ./third-party
# Generated media is gitignored; bind-mount or regenerate it for a real run.
COPY asset[s] ./assets

EXPOSE 8081

# The default serves CSS/JS uncompressed on purpose (defect UNCOMPRESSED-01).
CMD ["python", "server.py", "--port", "8081", "--root", "/app"]
