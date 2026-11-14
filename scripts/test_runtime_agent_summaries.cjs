'use strict';
// Offline behavioral test of the production renderer with the Agents panel hidden.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.argv[2], 'utf8');
function extract(name) {
  const start = source.indexOf('  function ' + name + '(');
  assert.ok(start >= 0, name + ' must exist');
  const end = source.indexOf('\n  function ', start + 1);
  return source.slice(start, end < 0 ? undefined : end);
}
function run(records, complete) {
  const nodes = new Map();
  const node = id => {
    if (!nodes.has(id)) nodes.set(id, {textContent:'Unknown',value:'all',dataset:{},
      setAttribute(){}, addEventListener(){}});
    return nodes.get(id);
  };
  const context = {snapshot:{sources:{agents_complete:complete}},selected:'main-1',
    visibleAgents:()=>records,renderTargetVisible:()=>false,$:node,
    active:a=>a.active===true,text:v=>String(v || ''),elapsed:()=> '5s',
    taskDisplayStatus:a=>a.active?'Working':'Idle'};
  vm.createContext(context);
  vm.runInContext(extract('summarizeAgentKinds')+'\n'+extract('renderAgents')+'\nrenderAgents();',context);
  return node;
}
let node = run([{id:'main-1',kind:'main',active:true,name:'Main agent'}],true);
assert.equal(node('agent-main-count').textContent,'1','Visible Task summary must update even when Agents list is hidden');
assert.equal(node('agent-subagent-count').textContent,'0','Complete observation with no children must report zero');
assert.match(node('agent-main-detail').textContent,/Working/);
assert.equal(node('agent-subagent-detail').textContent,'None reported');
node = run([{id:'main-1',kind:'main',active:true},{id:'child-1',kind:'subagent',active:true}],true);
assert.equal(node('agent-subagent-count').textContent,'1');
node = run([],false);
assert.equal(node('agent-main-count').textContent,'Unknown','Incomplete telemetry must not invent agents');
assert.equal(node('agent-subagent-count').textContent,'Unknown');
console.log('PASS: hidden-list summaries, main agent, no children, active child, incomplete telemetry');
