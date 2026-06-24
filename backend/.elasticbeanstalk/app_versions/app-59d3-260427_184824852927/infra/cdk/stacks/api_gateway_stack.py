import os
import json
from aws_cdk import (
    Stack,
    aws_apigateway as apigw,
    aws_certificatemanager as acm,
    CfnOutput
)
from constructs import Construct

class ApiGatewayStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, eb_endpoint: str, stage_name: str, domain_name: str = None, certificate_arn: str = None, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        
        openapi_path = os.path.join(os.path.dirname(__file__), '..', '..', 'openapi.json')
        
        with open(openapi_path, 'r') as f:
            spec = json.load(f)
            
        eb_endpoint = eb_endpoint.rstrip('/')
        
        paths = spec.get('paths', {})
        for path_key, methods in paths.items():
            for method_key, operation in methods.items():
                if method_key.lower() in ('get', 'post', 'put', 'delete', 'patch'):
                    integration_uri = f"{eb_endpoint}{path_key}"
                    
                    operation['x-amazon-apigateway-integration'] = {
                        "type": "http_proxy",
                        "httpMethod": method_key.upper(),
                        "uri": integration_uri,
                        "passthroughBehavior": "when_no_match",
                        "responses": {
                            "default": {"statusCode": "200"}
                        }
                    }
                    
                    if "parameters" in operation:
                        req_params = {}
                        for param in operation["parameters"]:
                            if param["in"] == "path":
                                req_params[f"integration.request.path.{param['name']}"] = f"method.request.path.{param['name']}"
                        if req_params:
                            operation['x-amazon-apigateway-integration']['requestParameters'] = req_params

            if 'options' not in methods:
                methods['options'] = {
                    "summary": "CORS preflight coverage",
                    "responses": {
                        "200": {
                            "description": "CORS enabled headers internally applied natively over options",
                            "headers": {
                                "Access-Control-Allow-Origin": {"schema": {"type": "string"}},
                                "Access-Control-Allow-Methods": {"schema": {"type": "string"}},
                                "Access-Control-Allow-Headers": {"schema": {"type": "string"}}
                            }
                        }
                    },
                    "x-amazon-apigateway-integration": {
                        "type": "mock",
                        "requestTemplates": {"application/json": "{\n  \"statusCode\": 200\n}"},
                        "responses": {
                            "default": {
                                "statusCode": "200",
                                "responseParameters": {
                                    "method.response.header.Access-Control-Allow-Headers": "'Content-Type,Authorization,X-Api-Key'",
                                    "method.response.header.Access-Control-Allow-Methods": "'OPTIONS,GET,POST,PUT,DELETE'",
                                    "method.response.header.Access-Control-Allow-Origin": "'*'"
                                }
                            }
                        }
                    }
                }

        domain_options = None
        if domain_name and certificate_arn:
            cert = acm.Certificate.from_certificate_arn(self, "ApiCert", certificate_arn)
            domain_options = apigw.DomainNameOptions(
                domain_name=domain_name,
                certificate=cert
            )
        
        api = apigw.SpecRestApi(self, "AskSaiVidyaApi",
            api_definition=apigw.ApiDefinition.from_inline(spec),
            deploy_options=apigw.StageOptions(
                stage_name=stage_name
            ),
            domain_name=domain_options
        )
        
        CfnOutput(self, "ApiUrl", value=api.url)
        CfnOutput(self, "ApiId", value=api.rest_api_id)
        if domain_name:
            CfnOutput(self, "CustomDomainUrl", value=f"https://{domain_name}")
