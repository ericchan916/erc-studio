'use strict';
const fs=require('node:fs'),path=require('node:path'),os=require('node:os'),cp=require('node:child_process');
const root=path.resolve(__dirname,'..'),dest=process.env.STUDIO_BUILD_DEST||path.join(os.homedir(),'Library/Application Support/ERC Studio/Builds/ERC Studio.app');
if(!path.isAbsolute(dest)||!dest.endsWith('.app'))throw new Error('build_destination_invalid');
const source=path.join(root,'node_modules/electron/dist/Electron.app');if(!fs.existsSync(source))throw new Error('electron_not_installed');
// Only replace our own reproducible build; profiles live outside Builds.
fs.rmSync(dest,{recursive:true,force:true});fs.mkdirSync(path.dirname(dest),{recursive:true,mode:0o700});cp.execFileSync('/usr/bin/ditto',[source,dest]);
const resources=path.join(dest,'Contents/Resources/app');fs.mkdirSync(resources,{recursive:true});
for(const name of ['src','web'])fs.cpSync(path.join(root,name),path.join(resources,name),{recursive:true});
fs.copyFileSync(path.join(root,'package.json'),path.join(resources,'package.json'));
const tabler=path.join(resources,'node_modules/@tabler/core');fs.mkdirSync(path.join(tabler,'dist/css'),{recursive:true});fs.copyFileSync(path.join(root,'node_modules/@tabler/core/dist/css/tabler.min.css'),path.join(tabler,'dist/css/tabler.min.css'));
for(const license of ['LICENSE','LICENSE.md'])if(fs.existsSync(path.join(root,'node_modules/@tabler/core',license)))fs.copyFileSync(path.join(root,'node_modules/@tabler/core',license),path.join(tabler,license));
const charts=path.join(resources,'node_modules/echarts');fs.mkdirSync(path.join(charts,'dist'),{recursive:true});fs.copyFileSync(path.join(root,'node_modules/echarts/dist/echarts.min.js'),path.join(charts,'dist/echarts.min.js'));
for(const license of ['LICENSE','NOTICE'])if(fs.existsSync(path.join(root,'node_modules/echarts',license)))fs.copyFileSync(path.join(root,'node_modules/echarts',license),path.join(charts,license));
const vendorLicenses=path.join(charts,'vendor-licenses');fs.mkdirSync(vendorLicenses,{recursive:true});for(const [dep,name]of [['zrender','LICENSE'],['tslib','LICENSE.txt']]){const license=path.join(root,'node_modules',dep,name);if(fs.existsSync(license))fs.copyFileSync(license,path.join(vendorLicenses,dep+'-LICENSE'));}
const plist=path.join(dest,'Contents/Info.plist');for(const [key,value] of [['CFBundleDisplayName','ERC Studio'],['CFBundleName','ERC Studio'],['CFBundleIdentifier','com.contentbase.studio.accounts'],['CFBundleShortVersionString','0.1.0'],['CFBundleVersion','20261001']]){try{cp.execFileSync('/usr/libexec/PlistBuddy',['-c',`Set :${key} ${value}`,plist],{stdio:'pipe'});}catch{cp.execFileSync('/usr/libexec/PlistBuddy',['-c',`Add :${key} string ${value}`,plist]);}}
cp.execFileSync('/usr/bin/codesign',['--force','--deep','--sign','-',dest]);cp.execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',dest]);console.log(JSON.stringify({ok:true,path:dest,signed:'local-ad-hoc',profile_data_preserved:true}));
