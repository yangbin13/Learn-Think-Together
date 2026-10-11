import asyncio

from backend.app.retrieval import Retriever, caption_identities, caption_priorities
from backend.tests.test_api import setup, register, wait_document


def test_caption_identity_handles_fullwidth_and_does_not_match_numeric_prefix():
    assert caption_identities("请看Ｆｉｇｕｒｅ ６．３．６，和表 D.4.2") == [("figure", "6.3.6"), ("table", "d.4.2")]
    assert caption_identities("图2-3所示的箭头") == [("figure", "2.3")]
    assert caption_priorities("Figure 6.3.6", [[("figure", "6.3.60")], [("example", "6.3.6")]]) == {1: 1}


def test_true_caption_is_prioritized_over_prose_reference_and_example_alias():
    text = "See Figure 4.2.3 for a drawing.\nFigure 4.2.4 Other drawing\nExample 4.2.3 Drawing task"
    assert caption_identities(text, headers_only=True) == [("figure", "4.2.4"), ("example", "4.2.3")]
    assert caption_priorities("Figure 4.2.3", [[("example", "4.2.3")], [("figure", "4.2.3")]]) == {1: 2}
    assert caption_priorities("Table 4.2.3", [[("figure", "4.2.3")]]) == {}


def test_real_retrieval_caption_location_preserves_course_ownership_and_deletion(setup):
    client, app, provider = setup
    user = register(client)
    course = client.post('/api/courses', json={'title': 'Caption location test'}).json()
    content = 'Chapter 6\nExample 6.3.6 Zero-force drawing\nActual image caption on this page.\n\n# Notes\nFigure 6.3.60 Different diagram\nUnrelated digit tokens.'
    response = client.post(f'/api/courses/{course["id"]}/documents', files={'file': ('captions.txt', content.encode(), 'text/plain')}, data={'rights_confirmed': 'true'})
    assert response.status_code == 202
    doc = wait_document(client, course['id'], response.json()['id'])
    assert doc['status'] == 'ready'
    retriever = Retriever(app.state.db, provider)
    result = asyncio.run(retriever.search(course['id'], user['id'], 'Figure 6.3.6', 1))
    assert result['items'] and 'Example 6.3.6' in result['items'][0]['text']
    assert '精确编号定位' in result['method'] and '实际原页图' in result['notice']
    assert asyncio.run(retriever.search(course['id'], 'another-user', 'Figure 6.3.6'))['items'] == []
    assert client.delete(f'/api/documents/{doc["id"]}').status_code in {200, 204}
    assert asyncio.run(retriever.search(course['id'], user['id'], 'Figure 6.3.6'))['items'] == []
