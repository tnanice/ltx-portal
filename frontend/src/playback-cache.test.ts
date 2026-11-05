import {it,expect,vi,beforeEach,afterEach} from 'vitest'
vi.mock('./api',()=>({api:vi.fn()}))
import {api} from './api'
import {preparePlayback,preparedPlayback,resetPlayback} from './playback-cache'
const result={url:'https://test/play/file',m3u_url:'https://test/play/file/m3u',name:'Video',expires:Date.now()/1000+600}
beforeEach(()=>{vi.stubGlobal('window',{dispatchEvent:vi.fn()});resetPlayback();vi.mocked(api).mockReset()})
afterEach(()=>vi.unstubAllGlobals())
it('deduplicates preparation of the same task/file across card and task views',async()=>{
 vi.mocked(api).mockResolvedValue(result)
 const [a,b]=await Promise.all([preparePlayback('t','p'),preparePlayback('t','p')])
 expect(a).toBe(b);expect(api).toHaveBeenCalledTimes(1)
 await preparePlayback('t','p');expect(api).toHaveBeenCalledTimes(1)
 expect(preparedPlayback('other','p')).toBeUndefined()
})
it('does not reuse credentials after logout or a changed binding',async()=>{
 let finish!:(value:unknown)=>void
 vi.mocked(api).mockImplementation(()=>new Promise(resolve=>{finish=resolve}))
 const request=preparePlayback('t','p');await Promise.resolve()
 resetPlayback();finish(result)
 await expect(request).rejects.toThrow('登录或绑定已变化')
 expect(preparedPlayback('t','p')).toBeUndefined()
})
it('limits concurrent upstream preparation to two',async()=>{
 const finishes:Array<(v:unknown)=>void>=[]
 vi.mocked(api).mockImplementation(()=>new Promise(resolve=>finishes.push(resolve)))
 const requests=['a','b','c'].map(t=>preparePlayback(t,'p'))
 await Promise.resolve();expect(api).toHaveBeenCalledTimes(2)
 finishes[0](result);finishes[1](result)
 await requests[0];await Promise.resolve()
 expect(api).toHaveBeenCalledTimes(3);finishes[2](result)
 await Promise.all(requests)
})
