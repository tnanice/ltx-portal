// @vitest-environment happy-dom
import {createApp,h,nextTick} from 'vue'
import {beforeEach,describe,expect,it,vi} from 'vitest'
import type {Task} from './api'

vi.mock('./playback-cache',()=>({
 preparePlayback:vi.fn(),
 preparedPlayback:vi.fn(()=>undefined)
}))
vi.mock('./players',async importOriginal=>{
 const actual=await importOriginal<typeof import('./players')>()
 return {...actual,launchPlayer:vi.fn(),copy:vi.fn()}
})

import PlayerButtons from './PlayerButtons.vue'
import {preparePlayback} from './playback-cache'
import {launchPlayer} from './players'

const task:Task={
 id:'task-1',resource:'TEST-001',title:'Test',state:'completed',percent:100,error:'',created:1,updated:1,
 files:[{pickcode:'pick-1',name:'video.mp4',size:1024}]
}
const playback={url:'https://example.test/play/tok',m3u_url:'https://example.test/play/tok/playlist.m3u',name:'video.mp4',expires:Date.now()/1000+3600}

async function flush(){
 await Promise.resolve()
 await nextTick()
 await Promise.resolve()
 await nextTick()
}

function render(){
 const host=document.createElement('div')
 document.body.appendChild(host)
 const app=createApp({render:()=>h(PlayerButtons,{task})})
 app.mount(host)
 return {host,unmount:()=>{app.unmount();host.remove()}}
}

beforeEach(()=>{vi.clearAllMocks();document.body.innerHTML=''})

describe('PlayerButtons on-demand playback',()=>{
 it('does not request playback tickets on mount and prepares only on click',async()=>{
  vi.mocked(preparePlayback).mockResolvedValue(playback)
  const {host,unmount}=render()
  await flush()
  expect(preparePlayback).not.toHaveBeenCalled()
  const btn=host.querySelector('button.play-button') as HTMLButtonElement
  expect(btn.disabled).toBe(false)
  btn.click()
  await flush()
  expect(preparePlayback).toHaveBeenCalledTimes(1)
  expect(preparePlayback).toHaveBeenCalledWith('task-1','pick-1')
  expect(launchPlayer).toHaveBeenCalledTimes(1)
  // Second click reuses cached playback without another backend request.
  btn.click()
  await flush()
  expect(preparePlayback).toHaveBeenCalledTimes(1)
  expect(launchPlayer).toHaveBeenCalledTimes(2)
  unmount()
 })

 it('shows retry button when on-demand preparation fails',async()=>{
  vi.mocked(preparePlayback).mockRejectedValueOnce(new Error('115繁忙')).mockResolvedValueOnce(playback)
  const {host,unmount}=render()
  ;(host.querySelector('button.play-button') as HTMLButtonElement).click()
  await flush()
  const retry=host.querySelector('button.playback-retry') as HTMLButtonElement
  expect(retry).not.toBeNull()
  expect(retry.title).toBe('115繁忙')
  retry.click()
  await flush()
  expect(host.querySelector('button.playback-retry')).toBeNull()
  expect(launchPlayer).toHaveBeenCalledTimes(1)
  unmount()
 })
})
