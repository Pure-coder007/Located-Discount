"""Address normalization shared by discovery and the public platform guide."""
import re
import unicodedata


def normalize_location(value):
    text = unicodedata.normalize('NFKD', value or '').casefold()
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.split())


def location_terms(value):
    ignored = {'nigeria', 'state', 'lga', 'local', 'government', 'area', 'road', 'street', 'avenue', 'rd', 'st', 'the', 'at', 'in', 'of'}
    terms = []
    for part in re.split(r'[,;/|]', value[:300]):
        words = [word for word in normalize_location(part).split() if word not in ignored and not word.isdigit()]
        if not words:
            continue
        phrase = ' '.join(words)
        if phrase not in terms:
            terms.append(phrase)
        if len(words) > 1:
            terms.extend(word for word in words if len(word) >= 3 and word not in terms)
    return terms[:12] or [normalize_location(value) or value[:100]]


def location_expression(column):
    # Portable SQL: tolerate punctuation, hyphens and repeated address spaces.
    result = f'LOWER({column})'
    for char in (',', '-', '.', '/', "'", '  '):
        escaped = char.replace("'", "''")
        result = f"REPLACE({result}, '{escaped}', ' ')"
    return result


def location_match_sql():
    return f'{location_expression("businesses.city")} LIKE ? OR {location_expression("businesses.address")} LIKE ?'
