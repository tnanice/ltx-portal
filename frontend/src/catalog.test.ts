import {describe,it,expect} from 'vitest'
import {primaryVideo,preferredTask,copyLinkLabel} from './catalog'
import type {Task} from './api'
const task={id:'one',resource:'TEST-123',state:'completed',updated:1,files:[]} as unknown as Task
describe('catalog playback',()=>{
 it('selects the clicked title, ignoring unrelated and sample videos',()=>{
  const t={...task,files:[{pickcode:'other',name:'OTHER-456.mkv',size:9999},{pickcode:'sample',name:'TEST-123 sample.mp4',size:99999},{pickcode:'main',name:'test_123.mp4',size:5000}]}
  expect(primaryVideo(t)?.pickcode).toBe('main')
 })
 it('uses the largest main video without a matching code and handles empty lists',()=>{
  expect(primaryVideo({...task,files:[{pickcode:'a',name:'movie.mp4',size:100},{pickcode:'b',name:'feature.mkv',size:1000}]} )?.pickcode).toBe('b')
  expect(primaryVideo(task)).toBeUndefined()
 })
 it('prefers a completed variant over a newer incomplete variant of the same title',()=>{
  expect(preferredTask([{...task,id:'pending',state:'downloading',updated:20},task,{...task,id:'unrelated',resource:'OTHER-1'}],'TEST-123')?.id).toBe('one')
 })
 it('retains the ed2k copy label even when a magnet appears first',()=>{
  expect(copyLinkLabel(['magnet:?xt=urn:btih:x','ed2k://|file|movie.mp4|1073741824|hash|/'])).toBe('复制 ed2k · 1.0G')
 })
})
