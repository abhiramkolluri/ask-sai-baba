import boto3

def main():
    print("=========================================")
    print("API Gateway Discovery Script Started")
    print("=========================================")
    
    # 1. API Gateway REST APIs (v1)
    client_v1 = boto3.client('apigateway')
    apis_v1 = client_v1.get_rest_apis().get('items', [])
    
    print("\n--- REST APIs (v1) ---")
    for api in apis_v1:
        api_id = api['id']
        name = api.get('name', 'Unknown')
        created = api.get('createdDate', 'Unknown')
        endpointConfiguration = api.get('endpointConfiguration', {}).get('types', [])
        
        print(f"\n[{name}] ID: {api_id} | Created: {created} | EndpointType: {endpointConfiguration}")
        
        # Resources for v1
        print("  Routes/Resources:")
        resources = client_v1.get_resources(restApiId=api_id).get('items', [])
        route_count_v1 = 0
        integration_urls = []
        for res in resources:
            path = res.get('path', '/')
            methods = res.get('resourceMethods', {})
            for method, method_data in methods.items():
                if method != "OPTIONS":
                    route_count_v1 += 1
                try:
                    integration = client_v1.get_integration(restApiId=api_id, resourceId=res['id'], httpMethod=method)
                    uri = integration.get('uri', 'No URI')
                    integration_type = integration.get('type', 'Unknown Type')
                    print(f"    - {method} {path} -> {integration_type} ({uri})")
                    if uri and uri not in integration_urls:
                        integration_urls.append(uri)
                except Exception as e:
                    print(f"    - {method} {path} -> (Integration error or none: {str(e)})")
                    
                    
        # Stages for v1
        print("  Stages:")
        stages = client_v1.get_stages(restApiId=api_id).get('item', [])
        for stage in stages:
            stage_name = stage.get('stageName', '')
            print(f"    - {stage_name}")

        print("  Summary:")
        print("    Type: REST v1")
        print(f"    ID: {api_id}")
        print(f"    Route count: {route_count_v1}")
        print(f"    Backend integrations: {', '.join(integration_urls)}")

    # 2. API Gateway HTTP APIs (v2)
    client_v2 = boto3.client('apigatewayv2')
    apis_v2 = client_v2.get_apis().get('Items', [])
    
    print("\n--- HTTP APIs (v2) ---")
    for api in apis_v2:
        api_id = api['ApiId']
        name = api.get('Name', 'Unknown')
        protocol = api.get('ProtocolType', 'Unknown')
        created = api.get('CreatedDate', 'Unknown')
        api_endpoint = api.get('ApiEndpoint', 'Unknown')
        
        print(f"\n[{name}] ID: {api_id} | Protocol: {protocol} | Created: {created}")
        print(f"  API Endpoint: {api_endpoint}")
        
        # Integrations for v2
        integrations = client_v2.get_integrations(ApiId=api_id).get('Items', [])
        integration_urls = []
        print("  Integrations:")
        for intg in integrations:
            intg_id = intg['IntegrationId']
            intg_uri = intg.get('IntegrationUri', 'No URI')
            intg_type = intg.get('IntegrationType', 'Unknown')
            print(f"    - [{intg_id}] {intg_type} -> {intg_uri}")
            if intg_uri not in integration_urls:
                integration_urls.append(intg_uri)
        
        # Routes for v2
        routes = client_v2.get_routes(ApiId=api_id).get('Items', [])
        print("  Routes:")
        for route in routes:
            route_key = route.get('RouteKey', '')
            print(f"    - {route_key}")

        # Stages for v2
        print("  Stages:")
        stages = client_v2.get_stages(ApiId=api_id).get('Items', [])
        stage_names = []
        for stage in stages:
            stage_name = stage.get('StageName', '$default')
            stage_names.append(stage_name)
            print(f"    - {stage_name}")

        print("  Summary:")
        print("    Type: HTTP v2")
        print(f"    ID: {api_id}")
        print(f"    Route count: {len(routes)}")
        print(f"    Backend integrations: {', '.join(integration_urls)}")


    # 5. List Custom Domains
    print("\n--- Custom Domain Names (v1) ---")
    domains_v1 = client_v1.get_domain_names().get('items', [])
    for d in domains_v1:
        domain_name = d.get('domainName')
        print(f"  - {domain_name}")

    print("\n--- Custom Domain Names (v2) ---")
    domains_v2 = client_v2.get_domain_names().get('Items', [])
    for d in domains_v2:
        domain_name = d.get('DomainName')
        print(f"  - {domain_name}")

    print("\n=========================================")
    print("API Gateway Discovery Complete")
    print("=========================================")

if __name__ == '__main__':
    main()
