import json
import re

def _extract_json(text: str):
    if not text:
        return {}
        
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
        
    # Try to find JSON block in markdown
    json_pattern = r"```(?:json)?\s*(\{.*?\})\s*```"
    match = re.search(json_pattern, text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
            
    # Try to find anything that looks like a JSON object using basic brace matching
    brace_pattern = r"(\{.*\})"
    match = re.search(brace_pattern, text, re.DOTALL)
    if match:
        try:
            content = match.group(1)
            return json.loads(content)
        except json.JSONDecodeError:
            pass
            
    raise ValueError("Could not extract valid JSON from AI response.")

test_cases = [
    '{"title": "Test"}',
    'Here is the JSON:\n```json\n{"title": "Test MD"}\n```',
    'Sure! {"title": "Test Mixed"} - hope you like it!',
    '```{"title": "Test MD No Lang"}```'
]

for i, test in enumerate(test_cases):
    try:
        print(f"Test {i}: {test[:20]}... -> {_extract_json(test)}")
    except Exception as e:
        print(f"Test {i} failed: {e}")
