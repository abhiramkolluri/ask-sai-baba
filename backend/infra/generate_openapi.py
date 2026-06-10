import os
import sys
import json
import inspect
import re

try:
    import yaml
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pyyaml"])
    import yaml

# Add the correctly mapped backend root to system PATH
backend_path = "/Users/abhiramkolluri/Projects/ask-sai-baba/backend"
sys.path.append(backend_path)

# Fallbacks to guarantee application boots cleanly over dynamic missing envs!
os.environ.setdefault("FLASK_ENV", "development")

try:
    from app import app as flask_app
except Exception as e:
    print(f"Error importing app: {e}")
    sys.exit(1)

def is_auth_protected(func_name):
    # Retrieve literal AST code definition via inspection searching natively for Python decorators
    try:
        source = inspect.getsource(flask_app.view_functions[func_name])
        return '@require_auth' in source
    except Exception:
        return False

def expects_json(func_name):
    try:
        source = inspect.getsource(flask_app.view_functions[func_name])
        return 'request.json' in source or 'request.is_json' in source
    except Exception:
        return False

spec = {
    "openapi": "3.0.0",
    "info": {
        "title": "Ask Sai Vidya API",
        "version": "2.0.0"
    },
    "servers": [
        {
            "url": "{gateway_url}",
            "variables": {
                "gateway_url": {
                    "default": "https://dxhp0j33db.execute-api.us-east-1.amazonaws.com/dev"
                }
            }
        }
    ],
    "components": {
        "securitySchemes": {
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT"
            }
        }
    },
    "paths": {}
}

custom_schemas = {
    "POST /search": {
        "req": {
            "type": "object",
            "properties": {"query": {"type": "string"}}
        },
        "res": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "_id": {"type": "string"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "score": {"type": "number"},
                    "location": {"type": "string"},
                    "occasion": {"type": "string"},
                    "link": {"type": "string"},
                    "collection": {"type": "string"}
                }
            }
        }
    },
    "POST /query": {
        "req": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "session_id": {"type": "string"},
                "user_id": {"type": "string"},
                "user_email": {"type": "string"}
            }
        },
        "res": {
            "type": "object",
            "properties": {
                "response": {"type": "string"},
                "session_id": {"type": "string"}
            }
        }
    },
    "GET /blog/{id}": {
        "res": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "location": {"type": "string"},
                "occasion": {"type": "string"},
                "link": {"type": "string"},
                "collection": {"type": "string"},
                "markdown_format": {"type": "string"}
            }
        }
    },
    "POST /summarize-question": {
        "req": {
            "type": "object",
            "properties": {"question": {"type": "string"}}
        },
        "res": {
            "type": "object",
            "properties": {"summary": {"type": "string"}}
        }
    },
    "POST /api/feedback": {
        "req": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "answer": {"type": "string"},
                "feedbackType": {"type": "string"},
                "reason": {"type": "string"},
                "additionalComments": {"type": "string"},
                "timestamp": {"type": "string"},
                "citations": {"type": "array", "items": {"type": "string"}}
            }
        },
        "res": {
            "type": "object",
            "properties": {"message": {"type": "string"}}
        }
    },
    "GET /chats/{user_email}": {
        "res": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "timestamp": {"type": "string"},
                    "messages": {"type": "array", "items": {"type": "object"}}
                }
            }
        }
    },
    "POST /chats/{user_email}": {
        "req": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "user_email": {"type": "string"}
            }
        },
        "res": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string"},
                "timestamp": {"type": "string"},
                "messages": {"type": "array", "items": {"type": "object"}}
            }
        }
    },
    "GET /": {
        "res": {
            "type": "object",
            "properties": {
                "status": {"type": "string"},
                "service": {"type": "string"},
                "version": {"type": "string"},
                "vector_store_healthy": {"type": "boolean"}
            }
        }
    }
}

num_operations = 0
all_routes = []

for rule in flask_app.url_map.iter_rules():
    if rule.endpoint == 'static':
        continue
        
    openapi_path = re.sub(r'<([^>:]+:)?([^>]+)>', r'{\2}', rule.rule)
    path_parameters = re.findall(r'\{([^}]+)\}', openapi_path)
    
    if openapi_path not in spec["paths"]:
        spec["paths"][openapi_path] = {}
        
    methods = [m for m in rule.methods if m not in ('OPTIONS', 'HEAD')]
    
    for method in methods:
        method_lower = method.lower()
        func_name = rule.endpoint
        docstring = flask_app.view_functions[func_name].__doc__
        
        operation = {
            "summary": func_name.replace("_", " ").title(),
            "description": docstring.strip() if docstring else f"Endpoint structure for {openapi_path}",
            "responses": {
                "400": {"description": "Bad Request"},
                "500": {"description": "Server Error"}
            }
        }
        
        # Security injection securely validated over `@require_auth`
        if is_auth_protected(func_name):
            operation["security"] = [{"bearerAuth": []}]
            operation["responses"]["401"] = {"description": "Unauthorized"}
            
        if path_parameters:
            operation["parameters"] = [
                {
                    "name": p,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"}
                } for p in path_parameters
            ]
            operation["responses"]["404"] = {"description": "Resource Not Found"}
            
        key = f"{method} {openapi_path}"
        custom_schema = custom_schemas.get(key)
        
        if expects_json(func_name) and method in ('POST', 'PUT', 'DELETE'):
            req_schema = {"type": "object"}
            if custom_schema and "req" in custom_schema:
                req_schema = custom_schema["req"]
                
            operation["requestBody"] = {
                "content": {
                    "application/json": {"schema": req_schema}
                }
            }
            
        res_schema = {"type": "object"}
        if custom_schema and "res" in custom_schema:
            res_schema = custom_schema["res"]
            
        operation["responses"]["200"] = {
            "description": "Successful Response",
            "content": {
                "application/json": {"schema": res_schema}
            }
        }
        
        spec["paths"][openapi_path][method_lower] = operation
        num_operations += 1
        all_routes.append(f"{method} {openapi_path}")

# --- Post-process: merge paths that only differ by path variable name ---
# API Gateway forbids sibling resources with different variable names at the
# same level (e.g. /chats/{user_email} vs /chats/{thread_id}).  Since EB is
# an http_proxy the parameter name is irrelevant — normalise to {id} and
# merge all methods under one path entry.
def _normalise_path(p):
    """Replace every {param_name} with {id}, {id2}, … positionally."""
    parts = p.split('/')
    idx = 0
    out = []
    for part in parts:
        if part.startswith('{') and part.endswith('}'):
            suffix = '' if idx == 0 else str(idx + 1)
            out.append('{id' + suffix + '}')
            idx += 1
        else:
            out.append(part)
    return '/'.join(out)

normalised_paths: dict = {}
for orig_path, methods in list(spec["paths"].items()):
    norm = _normalise_path(orig_path)
    if norm not in normalised_paths:
        normalised_paths[norm] = {}
    # Merge methods; later methods with the same verb overwrite earlier ones
    for method_key, operation in methods.items():
        # Rewrite parameter names to match the normalised path
        norm_params = re.findall(r'\{([^}]+)\}', norm)
        if "parameters" in operation:
            for i, param in enumerate(operation["parameters"]):
                if param["in"] == "path" and i < len(norm_params):
                    param["name"] = norm_params[i]
        normalised_paths[norm][method_key] = operation

spec["paths"] = normalised_paths

base_dir = os.path.dirname(os.path.abspath(__file__))
os.makedirs(base_dir, exist_ok=True)

with open(os.path.join(base_dir, 'openapi.yaml'), 'w') as f:
    yaml.dump(spec, f, sort_keys=False)
    
with open(os.path.join(base_dir, 'openapi.json'), 'w') as f:
    json.dump(spec, f, indent=2)

print("\n--- OpenAPI Generation Summary ---")
print(f"Total Configured Paths: {len(spec['paths'])}")
print(f"Total API Operations Evaluated: {num_operations}\n")
print("Compiled API Map:")
for r in sorted(all_routes):
    print(f" -> {r}")
