// @vitest-environment happy-dom
import {createApp,nextTick} from 'vue'
import {it,expect,vi} from 'vitest'
import App from './App.vue'
import {api} from './api'
vi.mock('./api',async importOriginal=>({...await importOriginal<typeof import('./api')>(),api:vi.fn()}))
it('shows the catalog before slow status/favorites finish and never refetches it on the first status snapshot',async()=>{
 vi.useFakeTimers();window.scrollTo=vi.fn()
 const pending:Record<string,(value:any)=>void>={}
 vi.mocked(api).mockImplementation((path:string)=>{
  if(path==='/auth/me')return Promise.resolve({user:{id:1,username:'test',role:'user'},csrf:'test'})
  if(path==='/catalog/items')return Promise.resolve({items:[],avatars:{}})
  return new Promise(resolve=>{pending[path]=resolve})
 })
 const root=document.createElement('div');document.body.append(root)
 const app=createApp(App);app.mount(root)
 const flush=async()=>{for(let i=0;i<12;i++){await Promise.resolve();await nextTick()}}
 await flush()
 expect(root.querySelector('.shell')).not.toBeNull()
 expect(root.textContent).not.toContain('正在打开你的资源库')
 expect(vi.mocked(api).mock.calls.filter(([p])=>p==='/catalog/items')).toHaveLength(1)
 pending['/me/favorites']([])
 pending['/catalog/status']({sync:{last_success:100},source:{}})
 pending['/me/115']({state:'ok',revision:1})
 pending['/me/tasks']([])
 await flush()
 expect(vi.mocked(api).mock.calls.filter(([p])=>p==='/catalog/items')).toHaveLength(1)
 app.unmount();root.remove();vi.useRealTimers()
})
