"""Labelled search results for ranking evaluation (tests/test_research_engine.py, docs/research/research-engine.md).

Each case is a query and hits in the order a provider returned them, each graded:
2 = answers the question (the authoritative page), 1 = related, 0 = irrelevant.
The provider orders are realistic failure shapes: a profile page or an aggregator
first, a namesake, stale versions, duplicates under tracking parameters.
"""

CASES = [
    {
        "query": "EchoSpeak latest updates",
        "hits": [
            ("Ty0x7 - GitHub", "https://github.com/Ty0x7", "Ty0x7 has 12 repositories available. Follow their code on GitHub.", 0),
            ("EchoSpeak hearing aid accessories", "https://shop.example.com/echospeak-hearing", "Buy hearing aid batteries and EchoSpeak covers.", 0),
            ("GitHub - Ty0x7/EchoSpeak: Personal agent runtime", "https://github.com/Ty0x7/EchoSpeak", "EchoSpeak is a local-first personal agent. Releases and pull requests.", 2),
            ("Releases · Ty0x7/EchoSpeak", "https://github.com/Ty0x7/EchoSpeak/releases", "EchoSpeak 11.11.2 Chat answers from evidence. 2026", 2),
            ("Echo (disambiguation) - Wikipedia", "https://en.wikipedia.org/wiki/Echo", "Echo may refer to a reflection of sound.", 0),
        ],
    },
    {
        "query": "python 3.14 release notes",
        "hits": [
            ("Python 3.12 release notes", "https://docs.python.org/3/whatsnew/3.12.html", "What's New In Python 3.12. 2023", 0),
            ("Python Release Python 3.14.0", "https://www.python.org/downloads/release/python-3140/?utm_source=x", "Python 3.14.0 is the newest major release.", 2),
            ("What's New In Python 3.14", "https://docs.python.org/3/whatsnew/3.14.html", "This article explains the new features in Python 3.14.", 2),
            ("python 3.14 - Pinterest", "https://www.pinterest.com/pin/123", "Python 3.14 snake pictures", 0),
            ("Python Release Python 3.14.0", "https://python.org/downloads/release/python-3140", "Duplicate of the release page.", 2),
        ],
    },
    {
        "query": "node.js 22 changelog",
        "hits": [
            ("Node.js 18 changelog", "https://github.com/nodejs/node/blob/main/doc/changelogs/CHANGELOG_V18.md", "Node.js 18 changes 2022", 0),
            ("Node.js — Node v22 release", "https://nodejs.org/en/blog/release/v22.0.0", "Node.js 22 changelog: require(esm), V8 12.4", 2),
            ("node 22 changelog - Quora", "https://www.quora.com/What-is-new-in-node-22", "What's new in node 22?", 1),
            ("CHANGELOG_V22.md", "https://github.com/nodejs/node/blob/main/doc/changelogs/CHANGELOG_V22.md", "Node.js 22 changelog", 2),
        ],
    },
    {
        "query": "kubernetes v1.34 deprecations",
        "hits": [
            ("Kubernetes Blog", "https://kubernetes.io/blog/", "Posts about Kubernetes releases", 1),
            ("Kubernetes v1.34 release: deprecations and removals", "https://kubernetes.io/blog/2026/07/kubernetes-v1-34-deprecations/", "Deprecations and removals in Kubernetes v1.34", 2),
            ("Deprecated API Migration Guide", "https://kubernetes.io/docs/reference/using-api/deprecation-guide/", "API removals by release, v1.34", 2),
            ("Learn Kubernetes in a day", "https://course.example.net/k8s", "Course for beginners", 0),
        ],
    },
    {
        "query": "gpt-5.5 api pricing",
        "hits": [
            ("OpenAI pricing explained (2023)", "https://blog.example.org/openai-pricing-2023", "GPT-4 pricing per token, 2023", 0),
            ("Pricing | OpenAI API", "https://platform.openai.com/docs/pricing", "GPT-5.5 input and output pricing per million tokens", 2),
            ("GPT-5.5 model | OpenAI API", "https://developers.openai.com/api/docs/models/gpt-5.5", "GPT-5.5 model card, context window and pricing", 2),
            ("ChatGPT Plus subscription", "https://openai.com/chatgpt/pricing", "ChatGPT plans for individuals", 1),
        ],
    },
    {
        "query": "who won the 2026 world cup final",
        "hits": [
            ("World Cup 2022 final: Argentina beat France", "https://sport.example.com/2022-final", "2022 final report", 0),
            ("2026 FIFA World Cup final report", "https://www.fifa.com/en/tournaments/mens/worldcup/canadamexicousa2026/final", "Final result and report, 2026 World Cup", 2),
            ("World Cup 2026 schedule", "https://sport.example.com/wc-2026-schedule", "Fixtures and kickoff times for the 2026 World Cup", 1),
            ("Football shirts sale", "https://shop.example.net/shirts", "Buy shirts", 0),
        ],
    },
    {
        "query": "BM25 ranking function explained",
        "hits": [
            ("Okapi BM25 - Wikipedia", "https://en.wikipedia.org/wiki/Okapi_BM25", "BM25 is a ranking function used by search engines", 2),
            ("Practical BM25 - Part 2: The BM25 Algorithm", "https://www.elastic.co/blog/practical-bm25-part-2-the-bm25-algorithm-and-its-variables", "How the BM25 ranking function works", 2),
            ("Ranking of football teams", "https://sport.example.com/rankings", "League tables and ranking", 0),
            ("Search engine basics", "https://www.example.edu/ir/basics", "An introduction to information retrieval", 1),
        ],
    },
    {
        "query": "requests python library latest version",
        "hits": [
            ("requests 2.31.0 docs (archived)", "https://docs.example.org/requests/2.31/", "Requests 2.31 documentation, 2023", 1),
            ("requests · PyPI", "https://pypi.org/project/requests/", "Python HTTP for Humans. Latest version", 2),
            ("Requests: HTTP for Humans", "https://requests.readthedocs.io/en/latest/", "Requests documentation, latest", 2),
            ("Python request for proposal template", "https://forms.example.com/rfp", "RFP template", 0),
        ],
    },
]
