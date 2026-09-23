# Generator Env
# --------
# opencode with qwen3.8-27b-fp8

import os
from sys import platform

SCENARIOS = {
    "litellm-GLM-5.2": {
        "credential_service": "tool_call_tester",
        "credential_username": "LLLM_VIRTUAL_API_KEY",
        "api_key_env": "LLLM_VIRTUAL_API_KEY",
        "base_url": "https://example.org/v1",
        "model": "GLM-5.2",
    },
}


def get_api_key(scenario):
    if platform == "win32":
        import keyring

        credential_service = scenario["credential_service"] or os.getenv("CRED_MAN_NAME")
        api_key = None

        if credential_service:
            api_key = keyring.get_password(
                credential_service,
                scenario["credential_username"],
            )

        api_key_env = scenario["api_key_env"]
        api_key = api_key or os.getenv(api_key_env)
        if not api_key:
            raise ValueError(
                "No API key found in Windows Credential Manager or environment "
                f"variable {api_key_env}."
            )

        return api_key, credential_service
    #TODO: to implement for linxu
    return "",""