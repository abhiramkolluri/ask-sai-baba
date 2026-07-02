import os
from aws_cdk import App
from stacks.api_gateway_stack import ApiGatewayStack

app = App()

eb_endpoint = os.environ.get("EB_ENDPOINT_URL", "http://asv-dev.eba-hsdbwmfy.us-east-1.elasticbeanstalk.com") # Default safe fallback internally matches discovery
stage_name = os.environ.get("STAGE_NAME", "staging")
domain_name = os.environ.get("DOMAIN_NAME")
certificate_arn = os.environ.get("CERTIFICATE_ARN")

ApiGatewayStack(
    app, 
    "AskSaiVidyaApiGateway",
    eb_endpoint=eb_endpoint,
    stage_name=stage_name,
    domain_name=domain_name,
    certificate_arn=certificate_arn
)

app.synth()
