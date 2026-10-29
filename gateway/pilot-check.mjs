import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Client, StreamableHTTPClientTransport} from '@modelcontextprotocol/client';
import {Client as LegacyClient} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport as LegacyTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
const api=process.env.ONTOBRIDGE_API_URL||'http://127.0.0.1:8788';
const mcp=new URL(process.env.ONTOBRIDGE_MCP_URL||'http://127.0.0.1:8791/mcp');
let token;
if(process.env.ONTOBRIDGE_MOCK_CREDENTIAL_FILE){
 token=JSON.parse(await readFile(process.env.ONTOBRIDGE_MOCK_CREDENTIAL_FILE,'utf8')).viewer;
}else{
 const config=await (await fetch(api+'/api/config')).json();
 assert.equal(config.demo,true,'Supply a dedicated synthetic pilot credential file for private mode');
 token=(await(await fetch(api+'/api/demo-session/viewer',{method:'POST'})).json()).token;
}
const headers={Authorization:'Bearer '+token,'Content-Type':'application/json'};
const rest=await(await fetch(api+'/api/v1/invoke/get_order_360',{method:'POST',headers,body:JSON.stringify({order_id:'SO-2026-0088'})})).json();
assert.equal(rest.result.progress.quantity,1200);
for(const era of ['modern','legacy']){
 const client=era==='modern'?new Client({name:'mock-pilot',version:'1'},{versionNegotiation:{mode:{pin:'2026-07-28'}}}):new LegacyClient({name:'mock-pilot-legacy',version:'1'});
 const transport=era==='modern'?new StreamableHTTPClientTransport(mcp,{requestInit:{headers}}):new LegacyTransport(mcp,{requestInit:{headers}});
 try{
  await client.connect(transport);
  if(era==='modern')assert.equal(client.getProtocolEra(),'modern');
  const tools=await client.listTools();assert.equal(tools.tools.length,6);assert.ok(!tools.tools.some(t=>t.name==='propose_schedule_change'));
  const response=await client.callTool({name:'get_order_360',arguments:{order_id:'SO-2026-0088'}});
  assert.deepEqual(response.structuredContent.result,rest.result);
  assert.deepEqual(response.structuredContent.meta.provenance,rest.meta.provenance);
  assert.equal(response.structuredContent.meta.capability_version,rest.meta.capability_version);
  const denied=await client.callTool({name:'propose_schedule_change',arguments:{order_id:'SO-2026-0088',extra_hours:2,note:'denied pilot',idempotency_key:'denied-pilot-001'}});
  assert.equal(denied.isError,true);assert.match(denied.content[0].text,/403/);
  console.log(`${era}: source reconciliation, catalog filtering, REST equivalence and role denial passed`);
 }finally{await client.close()}
}
