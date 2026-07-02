import boto3
from botocore.exceptions import ClientError

def discover():
    try:
        ag = boto3.client('apigateway')
        ag2 = boto3.client('apigatewayv2')
    except Exception as e:
        print(f"Error configuring boto3: {e}")
        return

    summary = []

    print("--- REST APIs (v1) ---")
    try:
        rest_apis = ag.get_rest_apis().get('items', [])
        for api in rest_apis:
            api_id = api['id']
            name = api.get('name')
            print(f"\n[REST] Name: {name} | ID: {api_id}")
            
            resources = ag.get_resources(restApiId=api_id).get('items', [])
            routes_count = len(resources)
            eb_urls = set()
            
            for res in resources:
                path = res.get('path')
                methods = res.get('resourceMethods', {})
                print(f"  Path: {path}")
                for method in methods.keys():
                    try:
                        integration = ag.get_integration(restApiId=api_id, resourceId=res['id'], httpMethod=method)
                        uri = integration.get('uri')
                        if uri:
                            eb_urls.add(uri)
                            print(f"    {method} -> {uri}")
                    except ClientError:
                        pass
            
            stages = ag.get_stages(restApiId=api_id).get('item', [])
            stage_names = [s.get('stageName') for s in stages]
            
            summary.append({
                "Type": "REST (v1)",
                "Name": name,
                "ID": api_id,
                "RoutesCount": routes_count,
                "BackendURLs": list(eb_urls),
                "Stages": stage_names
            })
    except Exception as e:
        print(f"Error getting REST APIs: {e}")

    print("\n--- HTTP APIs (v2) ---")
    try:
        http_apis = ag2.get_apis().get('Items', [])
        for api in http_apis:
            api_id = api['ApiId']
            name = api.get('Name')
            print(f"\n[HTTP] Name: {name} | ID: {api_id}")
            
            routes = ag2.get_routes(ApiId=api_id).get('Items', [])
            routes_count = len(routes)
            for route in routes:
                print(f"  Route: {route.get('RouteKey')}")
                
            integrations = ag2.get_integrations(ApiId=api_id).get('Items', [])
            eb_urls = set()
            for integ in integrations:
                uri = integ.get('IntegrationUri')
                if uri:
                    eb_urls.add(uri)
                print(f"  Integration: {uri}")
                
            stages = ag2.get_stages(ApiId=api_id).get('Items', [])
            stage_names = [s.get('StageName') for s in stages]
            
            summary.append({
                "Type": f"HTTP ({api.get('ProtocolType')})",
                "Name": name,
                "ID": api_id,
                "RoutesCount": routes_count,
                "BackendURLs": list(eb_urls),
                "Stages": stage_names
            })
    except Exception as e:
        print(f"Error getting HTTP APIs: {e}")

    print("\n--- Custom Domain Names ---")
    domains = []
    try:
        v1_domains = ag.get_domain_names().get('items', [])
        for d in v1_domains:
            name = d.get('domainName')
            domains.append(name)
            print(f"Domain: {name} -> {d.get('distributionDomainName')}")
    except Exception:
        pass
        
    try:
        v2_domains = ag2.get_domain_names().get('Items', [])
        for d in v2_domains:
            name = d.get('DomainName')
            domains.append(name)
            print(f"Domain: {name}")
    except Exception:
        pass

    print("\n================ SUMMARY ================")
    for s in summary:
        print(f"Gateway Type: {s['Type']}")
        print(f"Name:         {s['Name']}")
        print(f"ID:           {s['ID']}")
        print(f"Routes:       {s['RoutesCount']}")
        print(f"Backend URL:  {', '.join(s['BackendURLs']) if s['BackendURLs'] else 'None'}")
        print(f"Stages:       {', '.join(s['Stages']) if s['Stages'] else 'None'}")
        
    print(f"Custom Domains: {', '.join(domains) if domains else 'None'}")
    print("=========================================\n")

if __name__ == '__main__':
    discover()
