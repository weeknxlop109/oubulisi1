移动端 APP 接入契约（方案 A）
================================

后端只暴露三个接口，APP 最小壳按 JSON 收发即可，与网页共用同一服务实例。

1) GET /health
   作用：启动页展示组件状态灯。
   返回：{status, components:{graph,model,dict,question_bank,backup}, config, intents}

2) GET /demo/cases
   作用：首页生成快捷用例列表。
   返回：{cases:[{type,question,expect}], intents:[{intent,name,status,extension}]}

3) POST /ask
   请求：{"question": "风热感冒吃什么药", "session_id": "可选，多轮记忆用"}
   返回：
     status        ok / empty / clarify / safety / pending
     answer        模板答案文本
     entity        命中实体（规范名）
     intent        意图代码，intent_name 为中文名
     confidence    {ner, intent}
     evidence      {cypher, params, rows, path[], source, url}
     timings       {preprocess_ms, model_ms, query_ms, total_ms}
     trace         六步中间产物
     safety_note   安全提示（每条答案必带）

接入要求：
- 后端监听 0.0.0.0:8000，手机与电脑同一 Wi-Fi，使用 http://<本机IP>:8000。
- Android 使用 HTTP 明文需在 manifest 允许 usesCleartextTraffic；iOS 需允许本地网络访问。
- WebView 包装可直接复用 web/index.html；用 uni-app / Flutter 时按上面三个请求重写即可。
- 演示时 APP 与网页必须指向同一个服务实例，同一问句结果应当一致。
