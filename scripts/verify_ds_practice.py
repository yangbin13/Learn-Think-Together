"""Pure model-provenance helpers used by the quiz regression tests.

The historical private-account browser driver has been removed. This module
path remains for the existing test import; it performs no network or DB calls.
"""


import json


def returned_models(response):
    """Only upstream IDs in a creation response, not current configuration."""
    return {field: value if isinstance(value := response.get(field), str) and value.strip() else None
            for field in ('generation_model', 'review_model')}


def recover_creation_models(quiz_id, private_dir, prior_attempts):
    """Resume without generating or inventing missing historical metadata."""
    models = returned_models({})
    evidence = {field + '_evidence': 'unavailable' for field in models}
    for path in sorted(private_dir.glob('practice-*-generation-response.json'), reverse=True):
        try:
            response = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if not isinstance(response, dict) or response.get('id') != quiz_id:
            continue
        for field, value in returned_models(response).items():
            if models[field] is None and value is not None:
                models[field] = value
                evidence[field + '_evidence'] = 'private_creation_response:' + path.name
    for record in reversed(prior_attempts):
        if record.get('quiz_id') != quiz_id:
            continue
        for field, value in returned_models(record).items():
            if models[field] is None and value is not None:
                models[field] = value
                evidence[field + '_evidence'] = 'retained_attempt:' + str(record.get('scenario')) + ':' + str(record.get('attempt'))
    return {**models, **evidence}
