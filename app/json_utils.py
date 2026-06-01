import json
import re


def clean_json_string(text):
    """Clean and extract valid JSON array from LLM response."""
    # Remove thinking tags (various formats used by different models)
    # GLM, DeepSeek, Qwen, etc. use different thinking tag formats
    text = re.sub(r'<think>[\s\S]*?</think>', '', text)
    text = re.sub(r'<thinking>[\s\S]*?</thinking>', '', text)
    text = re.sub(r'<reflection>[\s\S]*?</reflection>', '', text)
    text = re.sub(r'<reasoning>[\s\S]*?</reasoning>', '', text)
    # Handle unclosed thinking tags (model started thinking but didn't close)
    text = re.sub(r'<think>[\s\S]*$', '', text)
    text = re.sub(r'<thinking>[\s\S]*$', '', text)

    # Remove markdown code blocks
    if "```" in text:
        # Find content between ```json and ``` or just ``` and ```
        match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if match:
            text = match.group(1).strip()

    # Find the JSON array - match from first [ to its closing ]
    # Use a bracket counter to find the correct closing bracket
    start = text.find('[')
    if start == -1:
        return None

    bracket_count = 0
    end = -1
    in_string = False
    escape_next = False

    for i, char in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue
        if char == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '[':
            bracket_count += 1
        elif char == ']':
            bracket_count -= 1
            if bracket_count == 0:
                end = i + 1
                break

    if end == -1:
        # No closing bracket found, try to salvage
        last_complete = text.rfind('},')
        if last_complete > start:
            return text[start:last_complete+1] + ']'
        return None

    json_text = text[start:end]

    # Clean control characters inside strings (common LLM issue)
    # Replace literal newlines/tabs inside JSON strings with escaped versions
    def fix_control_chars(match):
        s = match.group(0)
        # Replace unescaped control characters
        s = s.replace('\n', '\\n')
        s = s.replace('\r', '\\r')
        s = s.replace('\t', '\\t')
        return s

    # Fix control characters inside string values
    json_text = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_control_chars, json_text)

    return json_text


def repair_json_array(json_text):
    """Attempt to repair common JSON array issues from LLM output."""
    if not json_text:
        return None

    def _filter_entries(lst):
        """Keep only dict entries; LLMs sometimes emit bare strings in the array."""
        filtered = [e for e in lst if isinstance(e, dict)]
        if len(filtered) < len(lst):
            print(f"  Warning: Dropped {len(lst) - len(filtered)} non-object entries from LLM JSON array")
        return filtered if filtered else None

    # Try parsing as-is first
    try:
        result = json.loads(json_text)
        if isinstance(result, list):
            return _filter_entries(result)
    except json.JSONDecodeError:
        pass

    # Fix 1: Add missing commas between objects (}\s*{" -> },\n{")
    fixed = re.sub(r'\}\s*\{', '},\n{', json_text)
    try:
        result = json.loads(fixed)
        if isinstance(result, list):
            return _filter_entries(result)
    except json.JSONDecodeError:
        pass

    # Fix 2: Remove trailing commas before ]
    fixed = re.sub(r',\s*\]', ']', fixed)
    try:
        result = json.loads(fixed)
        if isinstance(result, list):
            return _filter_entries(result)
    except json.JSONDecodeError:
        pass

    # Fix 3: Try to extract individual entries and rebuild
    entries = []
    # Match individual JSON objects
    pattern = r'\{\s*"speaker"\s*:\s*"[^"]*"\s*,\s*"text"\s*:\s*"(?:[^"\\]|\\.)*"\s*,\s*"instruct"\s*:\s*"(?:[^"\\]|\\.)*"\s*\}'
    matches = re.findall(pattern, json_text, re.DOTALL)

    for match in matches:
        try:
            entry = json.loads(match)
            entries.append(entry)
        except json.JSONDecodeError:
            continue

    if entries:
        return entries

    # Fix 4: Last resort - find last complete entry and truncate
    last_complete = json_text.rfind('},')
    if last_complete > 0:
        try:
            truncated = json_text[:last_complete+1] + ']'
            # Ensure it starts with [
            if not truncated.strip().startswith('['):
                truncated = '[' + truncated
            result = json.loads(truncated)
            if isinstance(result, list):
                return _filter_entries(result)
        except json.JSONDecodeError:
            pass

    return None

def salvage_json_entries(json_text):
    """Last resort: extract individual valid entries with regex."""
    entries = []
    # Match individual JSON objects with speaker, text, instruct fields
    pattern = r'\{\s*"speaker"\s*:\s*"([^"]*)"\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"instruct"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}'
    matches = re.finditer(pattern, json_text, re.DOTALL)

    for match in matches:
        try:
            entry = {
                "speaker": match.group(1),
                "text": match.group(2).replace('\\"', '"').replace('\\n', '\n'),
                "instruct": match.group(3).replace('\\"', '"').replace('\\n', '\n')
            }
            entries.append(entry)
        except Exception:
            continue

    return entries if entries else None


def fix_mojibake(text):
    """Fix common mojibake characters resulting from CP1252-as-UTF8."""
    replacements = {
        'â€™': ''',  # Right single quote
        'â€˜': ''',  # Left single quote
        'â€œ': '"',  # Left double quote
        'â€\x9d': '"', # Right double quote
        'â€?': '"', # Sometimes ? if undefined
        'â€"': '—',  # Em dash
        'â€"': '–',  # En dash
        'â€¦': '…',  # Ellipsis
    }

    for bad, good in replacements.items():
        text = text.replace(bad, good)

    return text
