import {chromium} from '@playwright/test'
import {readFileSync,mkdirSync} from 'node:fs'
import {resolve} from 'node:path'
const env=Object.fromEntries(readFileSync('../.env','utf8').split('\n').filter(l=>l&&!l.startsWith('#')&&l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]))
const base=process.env.TEST_BASE_URL||`http://127.0.0.1:${env.APP_PORT||8896}`
const output=resolve('../artifacts');mkdirSync(output,{recursive:true})
const browser=await chromium.launch({headless:true,...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE}:{})})
const sizes=[{width:320,height:568},{width:375,height:667},{width:390,height:844},{width:430,height:932},{width:740,height:360}].filter(v=>!process.env.MOBILE_WIDTHS||process.env.MOBILE_WIDTHS.split(',').includes(String(v.width)))
const errors=[];let session
try{for(const viewport of sizes){
 const context=await browser.newContext({viewport,isMobile:true,hasTouch:true,deviceScaleFactor:1,...(session?{storageState:session}:{})})
 await context.route('**/api/playback/tickets',async route=>{if(route.request().method()!=='POST')return route.continue();await route.fulfill({json:{url:base+'/play/mobile-test-only',m3u_url:base+'/play/mobile-test-only/playlist.m3u',name:'Mobile test',expires:Date.now()/1000+600}})})
 const page=await context.newPage();page.on('pageerror',e=>{errors.push(e.message);console.log('page error',e.message)});page.on('response',async r=>{if(r.url().includes('/api/auth/login'))console.log('login status',r.status(),r.ok()?'OK':(await r.json()).detail)})
 const overflow=async(label)=>{if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw new Error(label+' horizontal overflow '+viewport.width)}
 const shot=async(name)=>{
  const dialog=page.getByRole('dialog')
  if(await dialog.count())return dialog.screenshot({path:resolve(output,`mobile-${viewport.width}-${name}.png`),mask:[dialog.locator('.detail-image'),dialog.locator('#detail-title'),dialog.locator('.detail>p')]})
  return page.screenshot({path:resolve(output,`mobile-${viewport.width}-${name}.png`),mask:[page.locator('.cover-button'),page.locator('.card-title'),page.locator('.actors'),page.locator('.task-info p')]})
 }

 await page.goto(base)
 if(!session){await page.getByLabel('账号',{exact:true}).fill(env.ADMIN_USERNAME);await page.getByLabel('密码',{exact:true}).fill(env.ADMIN_PASSWORD);await page.getByRole('button',{name:'进入资源库'}).click()}
 await page.locator('.catalog-toolbar').waitFor();if(!session)session=await context.storageState()
 await overflow('catalog')
 await page.locator('.catalog-controls').getByRole('button',{name:/已离线/}).click()
 const completedCount=await page.evaluate(async()=>{const rows=await (await fetch('/api/me/tasks')).json();return rows.filter(t=>t.state==='completed'&&t.files.length).length})
 if(completedCount){
  await page.locator('.player-row').first().waitFor();await page.locator('.player-row').first().scrollIntoViewIfNeeded()
  await page.locator('.player-row').first().locator('.player-icon.iina:not(:disabled)').waitFor()
  const boxes=await page.locator('.player-row').first().locator('button').evaluateAll(es=>es.map(e=>({w:e.getBoundingClientRect().width,h:e.getBoundingClientRect().height})))
  if(boxes.some(b=>b.h<43||b.w<39))throw new Error('Player targets too small '+JSON.stringify(boxes))
  await shot('players')
  await page.locator('.player-row').first().getByRole('button',{name:'更多播放选项'}).click()
  await page.getByRole('dialog').waitFor();await overflow('play dialog')
  if(await page.evaluate(()=>document.body.style.overflow!=='hidden'))throw new Error('Modal must lock background scrolling')
  await shot('play-dialog');await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
 }
 await page.locator('.catalog-controls').getByRole('button',{name:/已离线/}).click()
 await page.locator('.cover-button').first().click();await page.getByRole('dialog').waitFor();await overflow('detail');await shot('detail');await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
 await page.evaluate(()=>window.scrollTo(0,document.body.scrollHeight))
 const nav=await page.locator('nav[aria-label="主导航"]').boundingBox()
 if(!nav||Math.abs(nav.y+nav.height-viewport.height)>2)throw new Error('Bottom nav not anchored to viewport')
 await page.getByRole('button',{name:/我的任务/}).click();await page.getByRole('heading',{name:'个人任务库.'}).waitFor();await overflow('tasks');await shot('tasks')
 await page.getByRole('button',{name:'我的115',exact:true}).click();await page.getByRole('heading',{name:'我的 115'}).waitFor();await overflow('binding');await shot('binding')
 await page.getByRole('button',{name:'管理',exact:true}).click();await page.getByRole('heading',{name:'账号管理'}).waitFor();await overflow('admin');await shot('admin')
 const fieldSize=await page.getByLabel('初始密码').evaluate(e=>parseFloat(getComputedStyle(e).fontSize))
 if(fieldSize<16)throw new Error('Small form text can trigger iOS zoom')
 await page.getByRole('button',{name:env.ADMIN_USERNAME,exact:true}).click();await page.getByRole('heading',{name:'账号设置'}).waitFor()
 await page.setViewportSize({width:viewport.width,height:Math.min(viewport.height,400)})
 await page.getByLabel('新密码',{exact:true}).click();await overflow('keyboard-height dialog')
 const box=await page.getByRole('dialog').boundingBox();if(!box||box.y<0||box.y+box.height>Math.min(viewport.height,400)+1)throw new Error('Dialog outside reduced viewport')
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();await page.setViewportSize(viewport)
 if(viewport===sizes[sizes.length-1]){await page.getByRole('button',{name:'退出登录',exact:true}).click();await page.getByRole('heading',{name:'登录资源库'}).waitFor()}
 await context.close();console.log(`${viewport.width}x${viewport.height}: cards, 44px player targets, fixed nav, tasks, binding, admin, sheets, reduced viewport OK`)
}if(errors.length)throw new Error(errors.join('\n'))}finally{await browser.close()}
