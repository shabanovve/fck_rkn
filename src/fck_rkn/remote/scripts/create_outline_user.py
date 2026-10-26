import json
import re
import ssl
import sys
import urllib.request

user_name, state_directory = sys.argv[1:3]
with open(f'{state_directory}/access.txt', encoding='utf-8') as state:
    api_url = next((
        line.removeprefix('apiUrl:').strip()
        for line in state
        if line.startswith('apiUrl:')
    ), None)
if not api_url:
    raise RuntimeError('Outline management endpoint is unavailable')
context = ssl._create_unverified_context()
loopback_url = re.sub(r'^(https?://)\[?[^/]+\]?:(\d+)/', r'\g<1>127.0.0.1:\2/', api_url)
keys_endpoint = f'{api_url}/access-keys'
try:
    with urllib.request.urlopen(keys_endpoint, context=context, timeout=5) as response:
        keys = json.load(response).get('accessKeys')
except OSError:
    # The configured address may be a NAT address that is unreachable from the host.
    keys_endpoint = f'{loopback_url}/access-keys'
    with urllib.request.urlopen(keys_endpoint, context=context, timeout=30) as response:
        keys = json.load(response).get('accessKeys')
if not isinstance(keys, list):
    raise RuntimeError('Outline returned an invalid access-key list')
matching_keys = [key for key in keys if key.get('name') == user_name]
if len(matching_keys) > 1:
    raise RuntimeError('More than one Outline key has this user name')
if matching_keys:
    key = matching_keys[0]
else:
    request = urllib.request.Request(
        keys_endpoint,
        data=json.dumps({'name': user_name}).encode(),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    with urllib.request.urlopen(request, context=context, timeout=30) as response:
        key = json.load(response)
if key.get('name') != user_name:
    raise RuntimeError('Outline returned a key for a different user')
access_url = key.get('accessUrl')
if not isinstance(access_url, str) or not access_url.startswith('ss://'):
    raise RuntimeError('Outline did not return a valid access key')
print(access_url)
