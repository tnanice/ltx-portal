import {describe,it,expect,vi} from 'vitest'
import {players,platform,defaultPlayer,type Playback} from './players'
const playback:Playback={url:'https://example.test/play/a_b-c?x=1&y=2',m3u_url:'https://example.test/play/a/playlist.m3u',name:'中文 视频.mp4',expires:9999999999}
describe('player protocols',()=>{
 it('uses complete encoding for IINA and iOS VLC',()=>{
  for(const id of ['iina','vlc-ios']){
   const url=new URL(players.find(p=>p.id===id)!.build(playback))
   expect(url.searchParams.get('url')).toBe(playback.url)
  }
 })
 it('uses documented VidHub play endpoint and encoded title',()=>{
  const url=new URL(players.find(p=>p.id==='vidhub')!.build(playback))
  expect(url.pathname).toBe('/play');expect(url.searchParams.get('filename')).toBe(playback.name);expect(url.searchParams.get('url')).toBe(playback.url)
 })
 it('targets Android VLC explicitly',()=>{
  const url=players.find(p=>p.id==='vlc-android')!.build(playback)
  expect(url).toContain('package=org.videolan.vlc');expect(url).toContain('scheme=https;');expect(url).toContain('/play/a_b-c?x=1&y=2')
 })
 it('does not build broken nested PotPlayer http scheme',()=>{
  const p=players.find(p=>p.id==='potplayer')!;expect(p.clipboard).toBe(true);expect(p.build(playback)).toBe('potplayer:///clipboard')
 })
 it('detects tablets before macOS',()=>{
  vi.stubGlobal('navigator',{maxTouchPoints:5})
  expect(platform('Macintosh')).toBe('ios');expect(platform('Windows NT')).toBe('windows');expect(defaultPlayer('android')).toBe('vlc-android')
  vi.unstubAllGlobals()
 })
})

describe('inline launch',()=>{
 it('selects the Android VLC adapter for Android devices',async()=>{
  const {inlinePlayers}=await import('./players')
  expect(inlinePlayers('android').map(p=>p.id)).toEqual(['potplayer','iina','vlc-android','vidhub'])
 })
 it('copies before synchronous PotPlayer navigation and refuses failed clipboard writes',async()=>{
  const {launchPlayer}=await import('./players');const events:string[]=[]
  const input={style:{},value:'',select:vi.fn(),remove:vi.fn()}
  const exec=vi.fn(()=>{events.push('copy');return true})
  vi.stubGlobal('document',{createElement:()=>input,body:{append:vi.fn()},execCommand:exec})
  vi.stubGlobal('localStorage',{setItem:vi.fn()})
  vi.stubGlobal('window',{location:{set href(value:string){events.push(value)}}})
  try{
   launchPlayer(players[0],playback)
   expect(events).toEqual(['copy','potplayer:///clipboard'])
   events.length=0;exec.mockImplementation(()=>false)
   expect(()=>launchPlayer(players[0],playback)).toThrow('浏览器禁止复制')
   expect(events).toEqual([])
   expect(()=>launchPlayer(players[1],{...playback,expires:1})).toThrow('已过期')
  }finally{vi.unstubAllGlobals()}
 })
})
