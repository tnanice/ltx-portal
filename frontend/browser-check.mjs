// Read-only browser smoke test against a running local deployment.
// Credentials stay in memory; no storageState/trace containing sessions is persisted.
import {chromium} from '@playwright/test'
import {readFileSync,mkdirSync} from 'node:fs'
import {resolve} from 'node:path'
const env=Object.fromEntries(readFileSync('../.env','utf8').split('\n').filter(l=>l&&!l.startsWith('#')&&l.includes('=')).map(l=>[l.slice(0,l.indexOf('=')),l.slice(l.indexOf('=')+1)]))
const base=process.env.TEST_BASE_URL||`http://127.0.0.1:${env.APP_PORT||8896}`
const output=resolve('../artifacts');mkdirSync(output,{recursive:true})
const executable=process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
const browser=await chromium.launch({headless:true,...(executable?{executablePath:executable}:{})})
const errors=[]
try{
 for(const viewport of [{width:1440,height:1000},{width:390,height:844}]){
  const context=await browser.newContext({viewport})
  const page=await context.newPage()
  const prepared=[]
  await context.route('**/api/playback/tickets',async route=>{
   if(route.request().method()!=='POST')return route.continue()
   const body=route.request().postDataJSON();prepared.push(body)
   await route.fulfill({json:{url:base+'/play/browser-test-'+body.task_id,m3u_url:base+'/play/browser-test-'+body.task_id+'/playlist.m3u',name:'Browser verification',expires:Date.now()/1000+300}})
  })
  page.on('pageerror' ,e=>errors.push(e.message))
  await page.goto(base)
  await page.getByRole('heading',{name:'登录资源库'}).waitFor()
  await page.screenshot({path:resolve(output,`login-${viewport.width}.png`),fullPage:true})
  await page.getByLabel('账号',{exact:true}).fill(env.ADMIN_USERNAME)
  await page.getByLabel('密码',{exact:true}).fill(env.ADMIN_PASSWORD)
  await page.getByRole('button',{name:'进入资源库'}).click()
  await page.locator('.catalog-toolbar h1').waitFor()
  await page.getByLabel('搜索资源').fill('no-such-resource-zzzz')
  await page.getByRole('heading',{name:'没有匹配的作品'}).waitFor()
  await page.getByLabel('搜索资源').fill('')
  await page.locator('.card').first().waitFor()
  const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth+1)
  if(overflow)throw new Error(`Horizontal overflow at ${viewport.width}`)
  // Avoid putting private source imagery or titles into shareable screenshots.
  await page.screenshot({path:resolve(output,`catalog-${viewport.width}.png`),fullPage:false,mask:[page.locator('.cover-button'),page.locator('.card-title'),page.locator('.actors')]})
  const crop=await page.locator('.cover-button img').first().evaluate(e=>getComputedStyle(e).objectPosition)
  if(crop!=='100% 50%')throw new Error('Cover must crop the right side')
  if(!await page.locator('.copy-ed2k').first().innerText().then(t=>t.includes('复制')))throw new Error('Missing copy link')
  const taskData=await page.evaluate(async()=>await (await fetch('/api/me/tasks')).json())
  const readyCodes=new Set(taskData.filter(t=>t.state==='completed'&&t.files.length).map(t=>t.resource))
  await page.locator('.catalog-controls').getByRole('button',{name:/已离线/}).click()
  await page.waitForTimeout(100)
  const codes=await page.locator('.card').evaluateAll(es=>es.map(e=>e.getAttribute('data-code')))
  if(codes.some(c=>!readyCodes.has(c)))throw new Error('Completed filter includes incomplete title')
  if(codes.length){
   const card=page.locator('.card').first()
   await card.locator('.player-icon.iina:not(:disabled)').waitFor()
   const task=taskData.find(t=>t.resource===codes[0]&&t.state==='completed')
   if(!prepared.some(b=>b.task_id===task.id&&task.files.some(f=>f.pickcode===b.pickcode)))throw new Error('Playback selected a different title')
   const row=card.locator('.player-row')
   const bounds=await row.evaluate(e=>({width:e.clientWidth,scroll:e.scrollWidth}))
   if(bounds.scroll>bounds.width+1)throw new Error('Player icons overflow card')
   for(const [name,prefix] of [['IINA','iina:'],['PotPlayer','potplayer:'],['VLC · iOS','vlc-x-callback:'],['VidHub','open-vidhub:']]){
    // External protocol prompts can consume focus; isolate each launch in a fresh tab.
    const launchPage=await context.newPage()
    const cdp=await context.newCDPSession(launchPage);await cdp.send('Page.enable')
    const navigations=[];cdp.on('Page.frameRequestedNavigation',event=>navigations.push(event.url))
    await launchPage.goto(base);await launchPage.locator('.catalog-toolbar h1').waitFor()
    await launchPage.locator('.catalog-controls').getByRole('button',{name:/已离线/}).click()
    const launchRow=launchPage.locator('.card').first().locator('.player-row')
    await launchRow.locator('.player-icon.iina:not(:disabled)').waitFor()
    await launchRow.getByRole('button',{name:'用 '+name+' 播放',exact:true}).click()
    for(let i=0;i<20&&!navigations.some(url=>url.startsWith(prefix));i++)await launchPage.waitForTimeout(100)
    if(!navigations.some(url=>url.startsWith(prefix)))throw new Error('Protocol navigation not requested: '+name)
    if(await launchPage.getByRole('dialog').count())throw new Error('Unexpected player selection dialog: '+name)
    await launchPage.close()
   }
   await page.bringToFront()
   await page.screenshot({path:resolve(output,`players-${viewport.width}.png`),fullPage:false,mask:[page.locator('.cover-button'),page.locator('.card-title'),page.locator('.actors')]})
   await row.getByRole('button',{name:'更多播放选项'}).click()
   await page.getByRole('heading',{name:'在你的播放器中打开'}).waitFor()
   await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
  }
  await page.getByRole('button',{name:/我的任务/}).click()
  await page.getByRole('heading',{name:'个人任务库.'}).waitFor()
  await page.getByRole('button',{name:'我的115',exact:true}).click()
  await page.getByRole('heading',{name:'我的 115'}).waitFor()
  await page.getByRole('button',{name:'管理',exact:true}).click()
  await page.getByRole('heading',{name:'账号管理'}).waitFor()
  await page.getByRole('button',{name:'退出登录',exact:true}).click()
  await page.getByRole('heading',{name:'登录资源库'}).waitFor()
  await context.close()
  console.log(`browser ${viewport.width}x${viewport.height}: login, search, inline player protocols, fallback, navigation, logout, no overflow OK`)
 }
 if(errors.length)throw new Error(errors.join('\n'))
 console.log('No browser JavaScript errors. Screenshots contain no credentials.')
}finally{await browser.close()}
