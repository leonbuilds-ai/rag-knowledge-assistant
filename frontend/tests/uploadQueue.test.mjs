import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import ts from 'typescript'

const compiled = ts.transpileModule(readFileSync(new URL('../src/uploadQueue.ts', import.meta.url), 'utf8'), {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText
const { submitUploads } = await import('data:text/javascript;base64,' + Buffer.from(compiled).toString('base64'))
const rows = () => ['a','b','c'].map(id => ({ id, status:'waiting', attempts:0, error:'' }))

test('one failed upload does not discard later files; retry skips successes', async () => {
  const items=rows(), called=[]
  await submitUploads(items, async item => {called.push(item.id);if(item.id==='b')throw {status:500,message:'server error'};return {}}, new AbortController().signal)
  assert.deepEqual(called,['a','b','c'])
  assert.deepEqual(items.map(x=>x.status),['success','failed','success'])
  called.length=0
  await submitUploads(items,async item=>{called.push(item.id);return {duplicate:true}},new AbortController().signal)
  assert.deepEqual(called,['b']);assert.equal(items[1].status,'duplicate')
})
test('429 retries are bounded and preserve later files', async () => {
  const items=rows(), attempts=[], waits=[]
  await submitUploads(items,async item=>{attempts.push(item.id);if(item.id==='b')throw {status:429,retryAfter:3};return {}},new AbortController().signal,async ms=>{waits.push(ms)})
  assert.deepEqual(attempts,['a','b','b','b','c']);assert.deepEqual(waits,[3000,3000])
  assert.equal(items[1].status,'failed');assert.equal(items[2].status,'success')
})
test('pause retains unsent files for resume', async () => {
  const items=rows(), controller=new AbortController()
  await submitUploads(items,async item=>{if(item.id==='b'){controller.abort();throw Error('aborted')};return {}},controller.signal)
  assert.deepEqual(items.map(x=>x.status),['success','waiting','waiting'])
})
