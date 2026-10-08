FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Part 2: Elasticsearch client. Separate COPY+RUN so the expensive
# requirements.txt layer above stays cache-hit when only this changes.
COPY requirements-search.txt .
RUN pip install --no-cache-dir -r requirements-search.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
