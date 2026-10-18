"""Public-opinion monitoring: collect what people say in public (news, app reviews, other public pages), tag it (language, sentiment, topics) and show it
next to the project's interviews. Collection goes through connectors (connectors.py) over a safe fetch layer (safe_http.py); the research team never
sees authors' names, only a salted hash. See docs/舆情与采集_方案_v1.md."""
