"""Pure test-driver evidence recovery: no browser, provider or user database."""
import json

from scripts.verify_ds_practice import recover_creation_models, returned_models


def test_returned_models_do_not_infer_generation_from_main_configuration():
    assert returned_models({'model': 'main-model', 'configured_quiz_model': 'quiz-model'}) == {
        'generation_model': None, 'review_model': None}
    assert returned_models({'generation_model': 'actual-generator', 'review_model': 'actual-reviewer'}) == {
        'generation_model': 'actual-generator', 'review_model': 'actual-reviewer'}


def test_resume_recovers_only_matching_private_creation_response(tmp_path):
    (tmp_path / 'practice-drafts-attempt-1-generation-response.json').write_text(json.dumps({
        'id': 'target-quiz', 'generation_model': 'actual-quiz-model', 'review_model': 'actual-review-model'}), encoding='utf-8')
    (tmp_path / 'practice-drafts-attempt-2-generation-response.json').write_text(json.dumps({
        'id': 'other-quiz', 'generation_model': 'unrelated-model', 'review_model': 'unrelated-review'}), encoding='utf-8')
    (tmp_path / 'practice-drafts-attempt-3-generation-response.json').write_text('{', encoding='utf-8')
    restored = recover_creation_models('target-quiz', tmp_path, [{
        'quiz_id': 'target-quiz', 'expected_model': 'current-main', 'configured_quiz_model': 'current-quiz'}])
    assert restored['generation_model'] == 'actual-quiz-model' and restored['review_model'] == 'actual-review-model'
    assert restored['generation_model_evidence'].startswith('private_creation_response:')
    assert restored['review_model_evidence'].startswith('private_creation_response:')


def test_old_quiz_without_creation_metadata_remains_unknown_on_resume(tmp_path):
    (tmp_path / 'practice-drafts-attempt-1-generation-response.json').write_text(json.dumps({
        'id': 'legacy-quiz', 'questions': []}), encoding='utf-8')
    restored = recover_creation_models('legacy-quiz', tmp_path, [{
        'quiz_id': 'legacy-quiz', 'expected_model': 'main', 'configured_model': 'main',
        'configured_quiz_model': 'quiz', 'configured_review_model': 'review'}])
    assert restored == {'generation_model': None, 'review_model': None,
                        'generation_model_evidence': 'unavailable', 'review_model_evidence': 'unavailable'}


def test_resume_retains_known_prior_actual_models_without_changing_old_records(tmp_path):
    prior = [{'quiz_id': 'target', 'scenario': 'drafts', 'attempt': 1,
              'generation_model': 'known-generation', 'review_model': 'known-review'}]
    snapshot = json.dumps(prior, sort_keys=True)
    restored = recover_creation_models('target', tmp_path, prior)
    assert restored == {'generation_model': 'known-generation', 'review_model': 'known-review',
                        'generation_model_evidence': 'retained_attempt:drafts:1',
                        'review_model_evidence': 'retained_attempt:drafts:1'}
    assert json.dumps(prior, sort_keys=True) == snapshot


def test_private_creation_fields_take_precedence_and_do_not_replace_missing_with_current_config(tmp_path):
    (tmp_path / 'practice-default-attempt-1-generation-response.json').write_text(json.dumps({
        'id': 'target', 'generation_model': 'from-response', 'review_model': None}), encoding='utf-8')
    restored = recover_creation_models('target', tmp_path, [
        {'quiz_id': 'target', 'scenario': 'default', 'attempt': 1,
         'generation_model': 'older-record', 'configured_review_model': 'current-review'}])
    assert restored['generation_model'] == 'from-response'
    assert restored['review_model'] is None and restored['review_model_evidence'] == 'unavailable'


def test_nonstring_or_blank_actual_ids_are_not_model_evidence():
    assert returned_models({'generation_model': '', 'review_model': True}) == {
        'generation_model': None, 'review_model': None}
