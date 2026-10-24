#!/usr/bin/env python3
"""Narrow source/live hooks for the production frontend; no executor or data writes."""
import pathlib
def once(value,old,new):
    if value.count(old)!=1:raise ValueError('production_anchor_changed:'+old[:70])
    return value.replace(old,new)
def content(value):
    value=once(value,'<button class="content-tab" data-content-tab="reviews">','<button class="content-tab" data-content-tab="production">制作</button><button class="content-tab" data-content-tab="reviews">')
    value=once(value," window.addEventListener('resize',()=>{safely(()=>nativeProjects.resize());", " const productionUI=window.StudioProductionUI.create({host:root,toast});\n window.addEventListener('resize',()=>{safely(()=>nativeProjects.resize());")
    value=once(value,"if(document.body.classList.contains('studio-auth-locked')){safely(()=>nativeProjects.hide());", "if(document.body.classList.contains('studio-auth-locked')){productionUI.lock();safely(()=>nativeProjects.hide());")
    value=once(value,"function render(){const review=reviewEmbedded()", "function render(){const making=view==='production';productionUI.setVisible(making&&!root.hidden);if(making){projectRoot.hidden=true;reviewRoot.hidden=true;root.querySelector('.content-layout').hidden=true;root.querySelector('.content-source-footer').hidden=true;C('content-actor').hidden=C('content-refresh').hidden=false;C('content-actor').textContent='飞书候选 · '+(data.stale?'缓存快照':'只读');C('content-refresh').disabled=loading;root.querySelectorAll('[data-content-tab]').forEach(b=>b.classList.toggle('active',b.dataset.contentTab===view));safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());productionUI.update(data);return;}const review=reviewEmbedded()")
    return once(value,"root.hidden=true;safely(()=>nativeProjects.hide());document.querySelector('.topbar').hidden=false;", "root.hidden=true;productionUI.setVisible(false);safely(()=>nativeProjects.hide());document.querySelector('.topbar').hidden=false;") if "root.hidden=true;safely(()=>nativeProjects.hide());document.querySelector('.topbar').hidden=false;" in value else once(value,"root.hidden=true;safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());document.querySelector('.topbar').hidden=false;","root.hidden=true;productionUI.setVisible(false);safely(()=>nativeProjects.hide());safely(()=>nativeReviews?.hide());document.querySelector('.topbar').hidden=false;")
def index(value):
    value=once(value,'<script src="content-ui.js">','<script src="production-model.js"></script><script src="production-ui.js"></script><script src="content-ui.js">')
    value=once(value,'</head>','<link rel="stylesheet" href="production.css"></head>')
    return once(value,"form-action 'none'","form-action 'none'; media-src 'self' blob:")
def server(value):
    value=once(value,"target.suffix in ('.html','.js','.json','.zip')", "target.suffix in ('.html','.js','.json','.zip') or target.name=='production.css'")
    return once(value,"frame-src 'self' https:; object-src 'none'; frame-ancestors 'none'", "frame-src 'self' https:; media-src 'self' blob:; object-src 'none'; frame-ancestors 'none'")
if __name__=='__main__':
    root=pathlib.Path(__file__).resolve().parents[1]
    for rel,transform in [('web/content-ui.js',content),('web/index.html',index),('backend/server.py',server)]:
        p=root/rel;p.write_text(transform(p.read_text()))
