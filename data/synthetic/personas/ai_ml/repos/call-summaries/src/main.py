# call-summaries
from celery import Celery
app = Celery('jobs', broker='redis://')
import boto3
s3 = boto3.client('s3')
