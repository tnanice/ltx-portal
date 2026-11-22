// @vitest-environment happy-dom
import {createRenderer,h,nextTick} from 'vue'
import {describe,it,expect,vi,afterEach} from 'vitest'
import RetryImage from './RetryImage.vue'
type Node={type:string;props:Record<string,any>;children:Node[];parent?:Node;text?:string}
function setup(src='https://images.example/cover.jpg'){
 const node=(type:string):Node=>({type,props:{},children:[]})
 const renderer=createRenderer<Node,Node>({
  createElement:node,createText:t=>({...node('text'),text:t}),createComment:t=>({...node('comment'),text:t}),
  setText:(n,t)=>{n.text=t},setElementText:(n,t)=>{n.text=t;n.children=[]},
  parentNode:n=>n.parent||null,nextSibling:n=>n.parent?.children[n.parent.children.indexOf(n)+1]||null,
  insert(n,p,anchor){if(n.parent){n.parent.children.splice(n.parent.children.indexOf(n),1)}n.parent=p;const i=anchor?p.children.indexOf(anchor):-1;i<0?p.children.push(n):p.children.splice(i,0,n)},
  remove(n){if(n.parent)n.parent.children.splice(n.parent.children.indexOf(n),1)},
  patchProp(n,k,_old,v){n.props[k]=v}
 })
 const root=node('root')
 const render=(value:string)=>renderer.render(h(RetryImage,{src:value,alt:'cover'}),root)
 render(src)
 return {root,render,unmount:()=>renderer.render(null,root),image:()=>root.children[0].children.find(n=>n.type==='img')}
}
afterEach(()=>vi.useRealTimers())
describe('image recovery',()=>{
 it('retries twice then shows a fallback instead of looping',async()=>{
  vi.useFakeTimers();const view=setup()
  view.image()!.props.onError();await vi.advanceTimersByTimeAsync(1000);await nextTick()
  expect(view.image()!.props.src).toContain('_ltx_retry=1')
  view.image()!.props.onError();await vi.advanceTimersByTimeAsync(3000);await nextTick()
  expect(view.image()!.props.src).toContain('_ltx_retry=2')
  view.image()!.props.onError();await nextTick()
  expect(view.image()).toBeUndefined();expect(vi.getTimerCount()).toBe(0);view.unmount()
 })
 it('cancels pending retries on source changes and unmount',async()=>{
  vi.useFakeTimers();const view=setup();view.image()!.props.onError()
  view.render('https://images.example/new.jpg');await nextTick()
  await vi.advanceTimersByTimeAsync(10000);await nextTick();expect(view.image()!.props.src).toBe('https://images.example/new.jpg')
  view.image()!.props.onError();view.unmount();await vi.advanceTimersByTimeAsync(10000);expect(view.root.children).toHaveLength(0)
 })
 it('does not modify query strings that may carry signatures',async()=>{
  vi.useFakeTimers();const src='https://images.example/a.jpg?signature=abc';const view=setup(src)
  view.image()!.props.onError();await vi.advanceTimersByTimeAsync(1000);await nextTick()
  expect(view.image()!.props.src).toBe(src);view.unmount()
 })
})
