'use strict';
// Read-only source inspection, not authentication, a business mock or a provider.
const {app,BrowserWindow}=require('electron');
const path=require('node:path'),os=require('node:os'),fs=require('node:fs');
app.setName('ERC Studio Inspection');
const state=fs.mkdtempSync(path.join(os.tmpdir(),'contentbase-inspection-'));
app.setPath('userData',state);app.enableSandbox();
app.whenReady().then(()=>{
 const win=new BrowserWindow({width:1512,height:920,title:'ERC Studio · 源码只读查看',webPreferences:{nodeIntegration:false,contextIsolation:true,sandbox:true,webSecurity:true}});
 win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
 win.webContents.on('will-navigate',e=>e.preventDefault());
 win.loadFile(path.join(__dirname,'web/inspection.html'));
});
app.on('window-all-closed',()=>app.quit());
app.on('quit',()=>fs.rmSync(state,{recursive:true,force:true}));
