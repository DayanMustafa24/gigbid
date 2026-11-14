# model-deploy-pipeline
# .github/workflows/train.yml builds and pushes the model image
# k8s/deployment.yaml: 3 replicas, readiness probe on /healthz
import mlflow
mlflow.log_metric('mape', mape)
