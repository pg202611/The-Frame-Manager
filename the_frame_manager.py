#!/usr/bin/env python3
"""The Frame Manager 3.0.1 — bilingual Windows graphical interface."""
import logging, queue, threading, time, re
from tkinter import simpledialog
from frame_core import Preferences, DEFAULTS, MATTES, COLORS, FILTERS, valid_ip
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image

import frame_core as old
from frame_core import Registry, LEVELS, optimize, sha

DB = Registry(old.DATA)


def _tv_read_enriched():
    """All TV reads are non-destructive; never infer a per-photo Art filter."""
    tv=old.connect()
    try:
        ver=str(tv.get_api_version())
        photos=old.photos_unique(tv.available())
        extras={}
        for method in ('get_matte_list','get_photo_filter_list','get_artmode_settings','get_slideshow_status','get_device_info'):
            try:
                if method=='get_matte_list': value=getattr(tv,method)()
                else:value=getattr(tv,method)()
                extras[method]={'status':'ok','value':value}
            except Exception as exc:
                extras[method]={'status':'unavailable','error':str(exc)}
        return photos,ver,extras
    finally:
        try:tv.close()
        except Exception:pass


def _unpack_options(extras):
    mt=['none']; colors=[]; filt=['None']
    raw=extras.get('get_matte_list',{}).get('value')
    if isinstance(raw,(list,tuple)) and len(raw)>=2:
        mt=[v.get('matte_type',v) if isinstance(v,dict) else v for v in (raw[0] or [])]
        colors=[v.get('color',v) if isinstance(v,dict) else v for v in (raw[1] or [])]
    elif isinstance(raw,dict):
        mt=[v.get('matte_type',v) if isinstance(v,dict) else v for v in (raw.get('matte_type_list') or raw.get('matte_types') or [])]
        colors=[v.get('color',v) if isinstance(v,dict) else v for v in (raw.get('matte_color_list') or raw.get('matte_colors') or [])]
    raw=extras.get('get_photo_filter_list',{}).get('value')
    if isinstance(raw,list):filt=[v.get('filter_id',v) if isinstance(v,dict) else v for v in raw]
    elif isinstance(raw,dict):filt=[v.get('filter_id',v) if isinstance(v,dict) else v for v in raw.get('filter_list',[])]
    return list(map(str,mt or ['none'])),list(map(str,colors)),list(map(str,filt or ['None']))


def tv_change(cid,matte=None,portrait=None,filter_id=None):
    """Change only settings explicitly requested by the user.

IMPORTANT (2022 LS03B / Art API 4.3.4.0): do NOT pass
portrait_matte_id for landscape photos. Version 2.0 included this
argument on every change_matte call and Samsung returned error -7.
Per-photo filters cannot be read back via the Art inventory;
a successful command acknowledgment is not independent verification."""
    if matte is None and filter_id is None:
        raise ValueError('No changes requested')
    tv=old.connect();results={}
    try:
        before=old.photos_unique(tv.available())
        if cid not in before:
            raise RuntimeError(f'{cid} is absent from the TV; no changes sent')
        # Read Art Mode to help diagnose Samsung error -7 without
        # changing slideshow behavior.
        try:
            mode=tv.get_artmode()
            old.audit('artmode_before_edit',content_id=cid,status=mode)
            if isinstance(mode,str) and mode.lower() not in ('on','true','1'):
                raise RuntimeError('Art Mode does not appear active; no changes sent')
        except RuntimeError:
            raise
        except Exception as exc:
            old.audit('artmode_check_unavailable',content_id=cid,error=str(exc))
        current=before[cid].get('matte_id')
        if matte is not None:
            if current == matte:
                results['matte']='already applied; no command sent'
                old.audit('matte_change_skipped',content_id=cid,matte=matte)
            else:
                old.audit('matte_change_started',content_id=cid,requested=matte,
                          previous=current,portrait_parameter='omitted')
                # The Python API accepts portrait_matte as an optional argument.
                # Do NOT send it for landscape photos; preserve the existing value.
                tv.change_matte(cid,matte)
                for attempt in range(5):
                    if attempt:time.sleep(2)
                    after=old.photos_unique(tv.available())
                    if cid in after and after[cid].get('matte_id')==matte:
                        results['matte']='verified by reading TV state'
                        old.audit('matte_change_verified',content_id=cid,matte=matte,
                                  portrait_after=after[cid].get('portrait_matte_id'))
                        break
                if 'matte' not in results:
                    raise RuntimeError(f'{cid}: mat was not verified; filter was not sent')
        if filter_id is not None:
            old.audit('filter_change_started',content_id=cid,filter_id=filter_id)
            response=tv.set_photo_filter(cid,filter_id)
            if response is False:
                raise RuntimeError(f'{cid}: Samsung returned False for set_photo_filter')
            results['filter']='command accepted; effect cannot be verified by reading TV state'
            old.audit('filter_change_sent',content_id=cid,filter_id=filter_id,
                      result_type=type(response).__name__)
        return results
    finally:
        try:tv.close()
        except Exception:pass


def _upload_one(info,matte,filter_id=None,portrait_matte=None):
    old.verify_upload(info)
    tv=old.connect()
    try:
        before=old.photos_unique(tv.available())
        old.audit('upload_started',filename=info['name'],bytes=info['bytes'],matte=matte)
        cid=tv.upload(info['path'],matte=matte,portrait_matte=matte if portrait_matte is None else portrait_matte,file_type='jpg')
        old.audit('upload_ack',filename=info['name'],content_id=cid)
        if not isinstance(cid,str) or not re.fullmatch(r'MY_[A-Za-z0-9_-]+',cid):
            raise RuntimeError('Upload ID unverified; do not retry automatically')
        if cid in before:
            raise RuntimeError('Upload ID already existed before import; cannot verify a new photo')
        valid=False
        for attempt in range(6):
            if attempt:time.sleep(2)
            after=old.photos_unique(tv.available())
            if cid in after:
                if after[cid].get('matte_id')!=matte:
                    raise RuntimeError(f'{cid} exists, but has a different mat: {after[cid].get("matte_id")}')
                valid=True;break
        if not valid:raise RuntimeError(f'{cid} could not be found; do not retry automatically')
        old.audit('upload_verified',filename=info['name'],content_id=cid,matte=matte,new_id=cid not in before)
        # Apply the filter AFTER recording the SQLite source-to-TV link;
        # the association must survive any subsequent filter failure.
        return cid,after[cid]
    finally:
        try:tv.close()
        except Exception:pass











def tv_jpeg_info(rec, source_bytes=None, lang='en'):
    """Describe only the JPEG version actually recorded in SQLite.

    Missing upload metadata means unknown quality, not unoptimized.
    """
    rec=rec or {}
    qual=rec.get('uploaded_quality')
    sent_sha=rec.get('uploaded_sha256')
    sent_bytes=rec.get('uploaded_bytes')
    if qual in (80,85,90):
        label=f'JPEG {qual}'
        column=str(qual)
    elif sent_sha and isinstance(sent_bytes,int) and sent_bytes>0:
        label='None (uploaded without Manager optimization)'
        column='None'
    else:
        return '—', 'JPEG optimization: unknown (earlier upload not documented)'
    detail=f'JPEG optimization: {label}'
    if isinstance(sent_bytes,int) and sent_bytes>0:
        detail+=f' · {sent_bytes/1e6:.2f} MB'
        if qual in (80,85,90) and isinstance(source_bytes,int) and source_bytes>0:
            saving=(1-sent_bytes/source_bytes)*100
            detail+=f' · {saving:.1f}% smaller than source'
    return column,detail


def split_matte_id(matte):
    """Decode Samsung mat IDs without inferring a color when matte=none.

Missing or unrecognized values remain unknown and must not
be silently interpreted as no mat."""
    if matte == 'none':
        return 'none', None
    if not isinstance(matte,str) or '_' not in matte:
        return None, None
    style,color=matte.split('_',1)
    if style in MATTES and color in COLORS:
        return style,color
    return None,None


def aggregate_values(values,unknown, multiple):
    if not values:return unknown
    first=values[0]
    return first if all(v==first for v in values) else multiple


class ManagerV2(old.Manager):
    def __init__(self):
        # Load preferences BEFORE the legacy constructor, which refreshes TV state.
        self.prefs_store=Preferences(old.DATA)
        self.prefs=dict(self.prefs_store.data)
        self.lang='en'
        old.IP=self.prefs['ip']
        self.extras={}
        self.matte_types=['none'];self.matte_colors=[];self.filter_types=['None']
        self.settings_window=None
        self._editor_loading=False
        self._editor_baseline={}
        self._selected_tv_ids=()
        self._last_editor_color=None
        self._stop_batch=threading.Event()
        self._batch_running=False
        self._batch_kind=None
        self._column_save_after=None
        self._sort_state={'tv':None,'local':None}
        super().__init__()
        # At smaller sizes, fixed controls overlap the photo lists.
        self.minsize(920,680)
        # Ensure the Windows window remains explicitly resizable.
        # Minimum size protects the fixed controls in the lower panel.
        self.resizable(width=True, height=True)
        self.title('The Frame Manager 3.0.1 — Samsung The Frame')

    def quit_app(self):
        # Capture any just-completed column resize before closing.
        if hasattr(self,'tv_tree'):
            self._capture_column_widths('tv')
        if hasattr(self,'local_tree'):
            self._capture_column_widths('local')
        super().quit_app()

    def unchanged(self):
        return '(leave unchanged)'

    def quality_choices(self):
        return ['None','JPEG 90','JPEG 85','JPEG 80']

    def quality_canonical(self,value):
        return 'None' if value=='None' else value

    def current_default(self,key):
        value=self.prefs[key]
        return self.unchanged() if value=='(leave unchanged)' else ('None' if value=='None' else value)

    def unknown(self):
        return '(unknown)'

    def multiple(self):
        return '(multiple values)'

    def no_color(self):
        return '(no color)'

    def _apply_option_values(self):
        """Refresh options without discarding displayed mixed values."""
        styles=[self.unchanged(),self.unknown(),self.multiple()]+list(dict.fromkeys(list(self.matte_types)+list(MATTES[1:])))
        colors=[self.no_color(),self.multiple()]+list(dict.fromkeys(list(self.matte_colors)+list(COLORS)))
        filters=[self.unchanged(),self.unknown(),self.multiple()]+list(dict.fromkeys(list(self.filter_types)+list(FILTERS[1:])))
        self.style_combo.configure(values=list(dict.fromkeys(styles)))
        self.color_combo.configure(values=list(dict.fromkeys(colors)))
        self.filter_combo.configure(values=list(dict.fromkeys(filters)))

    def _initial_editor_values(self, ids):
        """Prefer TV-reported mat values; an unrecorded filter is unknown."""
        if not ids:
            return {'style':self.unchanged(),'color':self.prefs['matte_color'],
                    'filter':self.unchanged()}
        styles,colors,filters=[],[],[]
        for cid in ids:
            ob=self.tv_items.get(cid,{})
            style,color=split_matte_id(ob.get('matte_id'))
            styles.append(style or self.unknown())
            colors.append(self.no_color() if style=='none' else (color or self.unknown()))
            rec=DB.get_tv(cid) or {}
            if rec.get('filter_source') and rec.get('filter_id') is not None:
                filters.append(rec['filter_id'])
            else:
                filters.append(self.unknown())
        return {'style':aggregate_values(styles,self.unknown(),self.multiple()),
                'color':aggregate_values(colors,self.unknown(),self.multiple()),
                'filter':aggregate_values(filters,self.unknown(),self.multiple())}

    def _populate_editor(self,ids):
        """Displaying a selection must never initiate a TV change."""
        ids=tuple(ids)
        self._selected_tv_ids=ids
        values=self._initial_editor_values(ids)
        self._editor_loading=True
        try:
            self._apply_option_values()
            self.matte_style.set(values['style'])
            self.matte_color.set(values['color'])
            self.filter_setting.set(values['filter'])
            self._editor_baseline=dict(values)
            self._last_editor_color=(values['color'] if values['color'] in COLORS else self.prefs['matte_color'])
            for combo in (self.style_combo,self.color_combo,self.filter_combo):
                combo.state(['!disabled'] if ids else ['disabled'])
            self._sync_color_state()
        finally:
            self._editor_loading=False
        self._update_apply_button()

    def _sync_color_state(self):
        style=self.matte_style.get()
        if not self._selected_tv_ids or style=='none':
            if style=='none' and self.matte_color.get() in COLORS:
                self._last_editor_color=self.matte_color.get()
            if style=='none':self.matte_color.set(self.no_color())
            self.color_combo.state(['disabled'])
        else:
            self.color_combo.state(['!disabled'])
            if self.matte_color.get()==self.no_color() and style in set(self.matte_types)|set(MATTES[1:]):
                self.matte_color.set(self._last_editor_color or self.prefs['matte_color'])

    def _on_editor_change(self,*_):
        if self._editor_loading:return
        self._editor_loading=True
        try:self._sync_color_state()
        finally:self._editor_loading=False
        self._update_apply_button()

    def _field_changes(self):
        if not self._selected_tv_ids or not self._editor_baseline:return {}
        current={'style':self.matte_style.get(),'color':self.matte_color.get(),
                 'filter':self.filter_setting.get()}
        ignore={self.unchanged(),self.unknown(),self.multiple(),self.no_color()}
        changed={key:current[key]!=self._editor_baseline[key] and current[key] not in ignore
                 for key in current}
        if current['style']=='none':changed['color']=False
        return changed

    def _desired_updates(self,ids=None):
        """Compute per-photo changes without overwriting unedited fields.

Photos with no mat remain unchanged if only a mat color changes.
An unknown filter is never equivalent to the None filter."""
        ids=tuple(ids or self._selected_tv_ids)
        delta=self._field_changes()
        if not any(delta.values()):return []
        plans=[]
        for cid in ids:
            ob=self.tv_items[cid]; rec=DB.get_tv(cid) or {}
            current_matte=ob.get('matte_id')
            old_style,old_color=split_matte_id(current_matte)
            style=self.matte_style.get() if delta['style'] else old_style
            if style not in set(self.matte_types)|set(MATTES[1:]) and style!='none':
                if delta['style'] or delta['color']:
                    raise ValueError('Current mat style unknown; select a style explicitly.')
            matte=None
            if delta['style'] or delta['color']:
                if style=='none':target='none'
                elif style is not None:
                    if delta['color']:color=self.matte_color.get()
                    elif old_color in COLORS:color=old_color
                    else:color=self.prefs['matte_color']
                    if color not in COLORS:
                        raise ValueError('Invalid mat color.')
                    target=f'{style}_{color}'
                else:target=None
                if target is not None and target!=current_matte:matte=target
            filt=None
            if delta['filter']:
                target_filter=self.filter_setting.get()
                if not(rec.get('filter_source') and rec.get('filter_id')==target_filter):
                    filt=target_filter
            if matte is not None or filt is not None:
                plans.append((cid,matte,filt))
        return plans

    def _update_apply_button(self):
        if not hasattr(self,'apply_btn'):return
        enabled=bool(self._selected_tv_ids and any(self._field_changes().values()) and not self.busy)
        self.apply_btn.state(['!disabled'] if enabled else ['disabled'])

    def _tv_row(self,cid,obj,source_ids):
        rec=DB.get_tv(cid) or {}
        jpeg_col,_=tv_jpeg_info(rec,lang=self.lang)
        sid=source_ids.get(rec.get('local_path')) or '—'
        return (cid,sid,f"{obj.get('width','?')}×{obj.get('height','?')}",obj.get('matte_id','?'),jpeg_col)

    def _local_row(self,key,inf,source_ids):
        return (source_ids.get(str(Path(key).resolve())) or '—',inf['name'],
                f"{inf['width']}×{inf['height']}",'—')

    @staticmethod
    def _tree_column_widths(tree,widths):
        for col,width in zip(tree['columns'],widths):
            # Do not stretch every column to the entire window width:
            # Treeview must retain horizontal scrolling.
            tree.column(col,width=int(width),minwidth=50,stretch=False,anchor='w')

    def make_tree(self,parent,columns):
        """Keep both scrollbars attached to the visible Treeview area."""
        # The parent frame uses grid through the main layout.
        # Do not mix pack and grid inside the same parent.
        parent.rowconfigure(0,weight=1)
        parent.columnconfigure(0,weight=1)
        tree=ttk.Treeview(parent,columns=columns,show='headings',selectmode='extended')
        for col in columns:
            tree.heading(col,text=col)
            tree.column(col,width=110,minwidth=50,stretch=False)
        ybar=ttk.Scrollbar(parent,orient='vertical',command=tree.yview)
        xbar=ttk.Scrollbar(parent,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=ybar.set,xscrollcommand=xbar.set)
        tree.grid(row=0,column=0,sticky='nsew')
        ybar.grid(row=0,column=1,sticky='ns')
        xbar.grid(row=1,column=0,sticky='ew')
        ttk.Frame(parent,width=16,height=16).grid(row=1,column=1,sticky='nsew')
        return tree

    def _column_widths(self,side):
        tree=self.tv_tree if side=='tv' else self.local_tree
        return [int(tree.column(col,'width')) for col in tree['columns']]

    def _capture_column_widths(self,side):
        # Tk finalizes column-divider movement after ButtonRelease.
        if not self.winfo_exists():return
        try:
            values=self._column_widths(side)
            old_values=self.prefs_store.data['column_widths'][side]
            if values==old_values:return
            stored=self.prefs_store.save({
                'column_widths':{
                    'tv':values if side=='tv' else list(self.prefs_store.data['column_widths']['tv']),
                    'local':values if side=='local' else list(self.prefs_store.data['column_widths']['local']),
                }
            })
            self.prefs=stored
        except Exception as exc:
            logging.warning('Failed to save column widths %s: %s',side,exc)

    def _schedule_column_width_save(self,side):
        # Sort clicks trigger this event as well; _capture persists
        # widths only when a real change occurred.
        self.after(120,lambda:self._capture_column_widths(side))

    def _begin_batch(self,kind):
        """Enable a voluntary batch stop on the Tk main thread only."""
        if self.busy or self._batch_running:return False
        self._batch_running=True
        self._batch_kind=kind
        self._stop_batch.clear()
        self.stop_batch_btn.state(['!disabled'])
        return True

    def _check_stop_between_photos(self,action,completed,remaining):
        if not self._stop_batch.is_set():return False
        old.audit('batch_stop_requested',action=action,completed=completed,remaining=remaining)
        return True

    def _batch_end(self):
        self._batch_running=False
        self._batch_kind=None
        self.stop_batch_btn.state(['disabled'])

    def _enable_column_sort(self,tree,side):
        for ix,col in enumerate(tree['columns']):
            tree.heading(col,command=lambda i=ix,which=side:self._sort_tree(which,i))

    def _sort_tree(self,side,index=None):
        """Sort IDs and JPEG levels numerically; filenames naturally."""
        tree=self.tv_tree if side=='tv' else self.local_tree
        if index is None:
            state=self._sort_state.get(side)
            if state is None:return
            index,reverse=state
        else:
            prev=self._sort_state.get(side)
            reverse=(not prev[1]) if prev and prev[0]==index else False
            self._sort_state[side]=(index,reverse)
        def key(item):
            vals=tree.item(item,'values')
            v=str(vals[index]) if index<len(vals) else ''
            if not v or v in ('—','?'):
                return (1,0,())
            # Sort SRC-00012 and MY_F0042 by their numeric components.
            if (side=='tv' and index in (0,1)) or (side=='local' and index==0):
                m=re.search(r'(\d+)$',v)
                return (0,0,int(m.group(1)) if m else -1)
            if (side=='tv' and index==4):
                numeric={'None':0}
                return (0,0,int(v) if v.isdigit() else numeric.get(v,-1))
            if (side=='tv' and index==2) or (side=='local' and index==2):
                m=re.match(r'(\d+)×(\d+)',v)
                if m:return (0,0,(int(m.group(1))*int(m.group(2)),int(m.group(1))))
            if side=='local' and index==1:
                return (0,1,tuple((0,int(x)) if x.isdigit() else (1,x.casefold())
                                  for x in re.split(r'(\d+)',v)))
            return (0,1,v.casefold())
        ordered=sorted(tree.get_children(''),key=key,reverse=reverse)
        for i,item in enumerate(ordered): tree.move(item,'',i)
        # Show sort direction using an arrow in the column heading.
        cols=tree['columns']
        for i,col in enumerate(cols):
            label=col+(' ▼' if reverse else ' ▲') if index==i else col
            tree.heading(col,text=label)

    def _restore_lists(self,selected_tv=(),selected_local=()):
        # Rebuild table rows without modifying SQLite.
        self._apply_option_values()
        source_ids=DB.source_ids()
        for cid,obj in sorted(self.tv_items.items()):
            self.tv_tree.insert('','end',iid=cid,values=self._tv_row(cid,obj,source_ids))
        for key,inf in self.local_items.items():
            self.local_tree.insert('','end',iid=key,values=self._local_row(key,inf,source_ids))
        if self.local_items:self.mark_local_presence()
        self._sort_tree('tv');self._sort_tree('local')
        for cid in selected_tv:
            if self.tv_tree.exists(cid):self.tv_tree.selection_add(cid)
        for key in selected_local:
            if self.local_tree.exists(key):self.local_tree.selection_add(key)

    def _rebuild_interface(self):
        if self.busy:raise RuntimeError('Operation in progress')
        tvsel=self.tv_tree.selection();locsel=self.local_tree.selection()
        self._capture_column_widths('tv');self._capture_column_widths('local')
        for child in list(self.winfo_children()):
            if child is not self.settings_window:child.destroy()
        self.build()
        self._restore_lists(tvsel,locsel)
        self._populate_editor(self.tv_tree.selection())
        self.status.set('Settings applied.')
        self.title('The Frame Manager 3.0.1 — Samsung The Frame')

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift();self.settings_window.focus_set();return
        win=tk.Toplevel(self);self.settings_window=win
        win.title('Application settings')
        win.geometry('510x565');win.minsize(470,520)
        win.transient(self);win.grab_set()
        outer=ttk.Frame(win,padding=15);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text='Connection and interface',
                  font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(0,10))
        def field(label,value,choices=None):
            box=ttk.Frame(outer);box.pack(fill='x',pady=5)
            ttk.Label(box,text=label,width=27).pack(side='left')
            var=tk.StringVar(value=value)
            if choices is None:w=ttk.Entry(box,textvariable=var)
            else:w=ttk.Combobox(box,textvariable=var,values=choices,state='readonly')
            w.pack(side='left',fill='x',expand=True)
            return var
        ip=field('TV IP address',self.prefs['ip'])
        ttk.Separator(outer).pack(fill='x',pady=12)
        ttk.Label(outer,text='Default configurations',
                  font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(0,8))
        quality=field('JPEG optimization',
                      self.current_default('jpeg_quality'),
                      ['None','JPEG 90','JPEG 85','JPEG 80'])
        force=tk.BooleanVar(value=self.prefs['force_neutral'])
        ttk.Checkbutton(outer,text='Force no mat and no art effect when importing',
                        variable=force).pack(anchor='w',pady=(5,7))
        matte=field('Default mat style',
                    self.current_default('matte_style'),[self.unchanged()]+list(MATTES[1:]))
        color=field('Default mat color',
                    self.prefs['matte_color'],list(COLORS))
        effect=field('Default art effect',
                     self.current_default('art_filter'),[self.unchanged()]+list(FILTERS[1:]))
        ttk.Label(outer,text='Defaults preselect menu values; no changes are sent to the TV automatically.',
                  wraplength=470,foreground='#666',justify='left').pack(anchor='w',pady=(13,5))
        buttons=ttk.Frame(outer);buttons.pack(side='bottom',fill='x',pady=(12,0))
        def close():
            win.grab_release();win.destroy();self.settings_window=None
        def save():
            if self.busy:
                messagebox.showwarning('Operation in progress',
                    'Wait until the operation finishes before changing settings.',parent=win)
                return
            try:
                new_ip=valid_ip(ip.get())
            except ValueError:
                messagebox.showerror('Invalid IP address',
                    'Enter a valid IPv4 address, such as 192.168.1.100, without http:// or a port.',parent=win)
                return
            new={'ip':new_ip,'language':'en',
                 'jpeg_quality':self.quality_canonical(quality.get()),
                 'force_neutral':force.get(),
                 'matte_style':'(leave unchanged)' if matte.get()==self.unchanged() else matte.get(),
                 'matte_color':color.get(),
                 'art_filter':'(leave unchanged)' if effect.get()==self.unchanged() else effect.get()}
            try:stored=self.prefs_store.save(new)
            except Exception as exc:
                messagebox.showerror('Save error',str(exc),parent=win)
                return
            old_ip=old.IP
            self.prefs=stored;self.lang='en';old.IP=stored['ip']
            close()
            self.quality.set(self.current_default('jpeg_quality'))
            self.force.set(stored['force_neutral'])
            self._populate_editor(self.tv_tree.selection())
            self.ip_label.configure(text=f'   {old.MODEL}  ·  {old.IP}')
            if old_ip!=old.IP:
                # Keep the historical database but clear the current TV list
                # until the new device has been queried.
                self.tv_tree.delete(*self.tv_tree.get_children());self.tv_items={}
                self.status.set('IP address updated — reconnecting to the TV…')
                self.refresh_tv()
            else:
                self.status.set('Settings saved.')
        ttk.Button(buttons,text='Cancel',command=close).pack(side='right',padx=(8,0))
        ttk.Button(buttons,text='Save',command=save).pack(side='right')
        win.protocol('WM_DELETE_WINDOW',close)
        win.focus_set()

    def build(self):
        style=ttk.Style(self)
        if 'vista' in style.theme_names():style.theme_use('vista')
        top=ttk.Frame(self,padding=9);top.pack(fill='x')
        ttk.Label(top,text='THE FRAME MANAGER 3.0.1',font=('Segoe UI',16,'bold')).pack(side='left')
        self.ip_label=ttk.Label(top,text=f'   {old.MODEL}  ·  {old.IP}',foreground='#666');self.ip_label.pack(side='left')
        ttk.Button(top,text='Settings…',command=self.open_settings).pack(side='right',padx=4)
        ttk.Button(top,text='Backup',command=self.make_backup).pack(side='right',padx=4)
        ttk.Button(top,text='Export CSV',command=self.make_export).pack(side='right',padx=4)
        ttk.Button(top,text='Refresh TV',command=self.refresh_tv).pack(side='right',padx=4)
        # IMPORTANT: pack the status footer BEFORE the expanding content pane.
        # In v2.0.4 the status label was packed last; on Windows the
        # main pane consumed the entire height and the footer
        # was not even mapped (reproduced at 1240×780 in Tk 8.6).
        # Packing the bottom footer first ensures it remains visible.
        # Bottom strip: expanding status text and a visible resize handle.
        # Sizegrip enables resizing from the lower-right corner even
        # if the Windows theme provides only a very thin window border.
        # Pack the footer BEFORE packing the expanding main pane.
        self.status_footer=ttk.Frame(self)
        self.status_footer.pack(side='bottom',fill='x')
        self.status_bar=ttk.Label(self.status_footer,textvariable=self.status,
                                  relief='sunken',anchor='w',padding=6)
        self.status_bar.pack(side='left',fill='x',expand=True)
        self.resize_grip=ttk.Sizegrip(self.status_footer)
        self.resize_grip.pack(side='right',anchor='se',padx=(3,2),pady=(0,2))
        pane=ttk.Frame(self,padding=(9,3));pane.pack(fill='both',expand=True)
        pane.columnconfigure(0,weight=1);pane.columnconfigure(2,weight=1);pane.rowconfigure(0,weight=1)
        left=ttk.LabelFrame(pane,text='Photos on The Frame',padding=7);left.grid(row=0,column=0,sticky='nsew')
        mid=ttk.Frame(pane,padding=7);mid.grid(row=0,column=1,sticky='ns')
        right=ttk.LabelFrame(pane,text='Local JPEGs — original files untouched',padding=7);right.grid(row=0,column=2,sticky='nsew')
        # Photo lists expand with the window; detail panels remain
        # almost fixed-height at the bottom.
        left.columnconfigure(0,weight=1);left.rowconfigure(0,weight=1)
        right.columnconfigure(0,weight=1);right.rowconfigure(0,weight=1)
        tv_list_host=ttk.Frame(left);tv_list_host.grid(row=0,column=0,sticky='nsew')
        local_list_host=ttk.Frame(right);local_list_host.grid(row=0,column=0,sticky='nsew')
        self.tv_tree=self.make_tree(tv_list_host,('TV ID','Source ID','Dimensions','Mat style','JPEG'))
        self._tree_column_widths(self.tv_tree,self.prefs_store.data['column_widths']['tv'])
        self.tv_tree.bind('<ButtonRelease-1>',lambda e:self._schedule_column_width_save('tv'),add='+')
        self._enable_column_sort(self.tv_tree,'tv')
        # Keep the table compact (minimum six rows) but expandable.
        # At 1240×780 the detail panels and controls remain visible
        # above the status bar.
        self.tv_tree.configure(height=6)
        self.local_tree=self.make_tree(local_list_host,('Source ID','File','Dimensions','TV'))
        self._tree_column_widths(self.local_tree,self.prefs_store.data['column_widths']['local'])
        self.local_tree.bind('<ButtonRelease-1>',lambda e:self._schedule_column_width_save('local'),add='+')
        self._enable_column_sort(self.local_tree,'local')
        self.local_tree.configure(height=6)
        self.tv_tree.bind('<<TreeviewSelect>>',lambda _ :self.show_selected('tv'))
        self.local_tree.bind('<<TreeviewSelect>>',lambda _ :self.show_selected('local'))
        ttk.Button(right,text='Select source folder…',command=self.choose_folder).grid(row=1,column=0,sticky='ew',pady=6)
        ttk.Label(mid,text='Transfer',font=('Segoe UI',10,'bold')).pack(pady=(32,10))
        ttk.Label(mid,text='JPEG optimization').pack(anchor='w')
        self.quality=tk.StringVar(value=self.current_default('jpeg_quality'))
        ttk.Combobox(mid,width=16,values=self.quality_choices(),state='readonly',textvariable=self.quality).pack(fill='x',pady=3)
        self.force=tk.BooleanVar(value=self.prefs['force_neutral'])
        ttk.Checkbutton(mid,text='Force no mat\nand no effect (imports)',variable=self.force).pack(pady=(4,8))
        self.upload_btn=ttk.Button(mid,text='← Import',command=self.upload_selected);self.upload_btn.pack(fill='x',pady=4)
        self.delete_btn=ttk.Button(mid,text='✕ Delete',command=self.delete_selected);self.delete_btn.pack(fill='x',pady=4)
        ttk.Separator(mid,orient='horizontal').pack(fill='x',pady=(13,8))
        ttk.Label(mid,text='Batch operations',font=('Segoe UI',9,'bold')).pack(pady=(2,4))
        self.stop_batch_btn=ttk.Button(mid,text='Stop after current photo',command=self.request_batch_stop)
        self.stop_batch_btn.pack(fill='x',pady=(2,4))
        self.stop_batch_btn.state(['disabled'])

        ttk.Label(mid,text='Ctrl / Shift:\nmultiple selection',justify='center').pack(pady=9)
        edit=ttk.LabelFrame(left,text='TV details and settings editor',padding=7)
        edit.grid(row=1,column=0,sticky='ew',pady=(8,0))
        # Keep the preview slot the same height while thumbnails
        # load and after they appear (maximum 320×150).
        # pack_propagate(False) prevents images from resizing this panel.
        self.tv_preview_slot=ttk.Frame(edit,width=340,height=168)
        self.tv_preview_slot.pack(fill='x')
        self.tv_preview_slot.pack_propagate(False)
        self.tv_preview=ttk.Label(self.tv_preview_slot,text='TV thumbnail',anchor='center')
        self.tv_preview.pack(fill='both',expand=True)
        # Use three detail lines with stable height for every selection.
        self.tv_detail_slot=ttk.Frame(edit,height=63)
        self.tv_detail_slot.pack(fill='x',pady=(5,0))
        self.tv_detail_slot.pack_propagate(False)
        self.tv_detail=ttk.Label(self.tv_detail_slot,text='Select a photo.',justify='left',anchor='nw')
        self.tv_detail.pack(fill='both',expand=True)
        self.tv_preview_status=ttk.Label(edit,text='Thumbnail: —',foreground='#666')
        self.tv_preview_status.pack(anchor='w',pady=(0,5))
        self.matte_style=tk.StringVar(value=self.current_default('matte_style'))
        self.matte_color=tk.StringVar(value=self.current_default('matte_color'))
        self.filter_setting=tk.StringVar(value=self.current_default('art_filter'))
        params=ttk.Frame(edit);params.pack(fill='x');params.columnconfigure(1,weight=1)
        ttk.Label(params,text='Mat style').grid(row=0,column=0,sticky='w')
        self.style_combo=ttk.Combobox(params,textvariable=self.matte_style,state='readonly',values=[self.unchanged(),'none']);self.style_combo.grid(row=0,column=1,sticky='ew')
        ttk.Label(params,text='Color').grid(row=1,column=0,sticky='w')
        self.color_combo=ttk.Combobox(params,textvariable=self.matte_color,state='readonly',values=['polar']);self.color_combo.grid(row=1,column=1,sticky='ew')
        ttk.Label(params,text='Art effect').grid(row=2,column=0,sticky='w')
        self.filter_combo=ttk.Combobox(params,textvariable=self.filter_setting,state='readonly',values=[self.unchanged(),'None']);self.filter_combo.grid(row=2,column=1,sticky='ew')
        self.apply_btn=ttk.Button(edit,text='Apply to selected photos',command=self.change_selected)
        self.apply_btn.pack(fill='x',pady=(6,1))
        self.apply_btn.state(['disabled'])
        for var in (self.matte_style,self.matte_color,self.filter_setting):
            var.trace_add('write',self._on_editor_change)
        ttk.Label(edit,text='Only selected settings are changed.\nPer-photo effect cannot be read back.',foreground='#666').pack(anchor='w')
        local=ttk.LabelFrame(right,text='Local file details',padding=7)
        local.grid(row=2,column=0,sticky='ew',pady=(8,0))
        self.local_preview_slot=ttk.Frame(local,height=168)
        self.local_preview_slot.pack(fill='x')
        self.local_preview_slot.pack_propagate(False)
        self.local_preview=ttk.Label(self.local_preview_slot,text='Windows thumbnail',anchor='center')
        self.local_preview.pack(fill='both',expand=True)
        self.local_detail_slot=ttk.Frame(local,height=88)
        self.local_detail_slot.pack(fill='x',pady=4)
        self.local_detail_slot.pack_propagate(False)
        self.local_detail=ttk.Label(self.local_detail_slot,text='Select a photo.',justify='left',anchor='nw')
        self.local_detail.pack(fill='both',expand=True)
        self.tv_ref=None;self.local_ref=None
        self._populate_editor(())
        # The footer was already packed before the main title bar.

    def background(self,job,on_success=None):
        # Block TV commands while another operation is running.
        super().background(job,on_success)
        self._update_apply_button()

    def refresh_tv(self):
        self.status.set('Reading TV library and details…')
        self.background(_tv_read_enriched,self.update_tv)

    def update_tv(self,result):
        if len(result)==3:photos,version,extras=result
        else:photos,version=result;extras=self.extras
        selected_before=self.tv_tree.selection()
        self.tv_items=photos;self.extras=extras
        DB.tv_inventory(photos)
        if extras:
            mt,colors,filts=_unpack_options(extras)
            self.matte_types=mt;self.matte_colors=colors;self.filter_types=filts
            self._apply_option_values()
        self.tv_tree.delete(*self.tv_tree.get_children())
        source_ids=DB.source_ids()
        for cid,obj in sorted(photos.items()):
            self.tv_tree.insert('', 'end',iid=cid,values=self._tv_row(cid,obj,source_ids))
        self._sort_tree('tv')
        for cid in selected_before:
            if self.tv_tree.exists(cid):self.tv_tree.selection_add(cid)
        self._populate_editor(self.tv_tree.selection())
        if self.local_items:
            DB.import_old_logs(old.EVENTS)
            self.mark_local_presence()
        self.status.set(f'TV: {len(photos)} photos · API {version} · database updated')
        old.audit('library_read',count=len(photos),api_version=version)

    def choose_folder(self):
        if self.busy:
            messagebox.showinfo('Operation in progress',
                                'Wait until the current operation finishes before changing folders.')
            return
        # The Windows folder dialog does not reveal the file count until
        # the user closes it. Display indexing status immediately after
        # selection, then stream progress updates from the worker.
        folder=filedialog.askdirectory(title='Source JPEG folder')
        if not folder:return
        source_dir=Path(folder)
        self.folder=source_dir
        self.status.set(f'Preparing to index folder {source_dir.name}…')
        self.update_idletasks()  # Make the status visible before starting the worker thread.

        def task():
            found={};errors=[]
            files=[p for p in sorted(source_dir.iterdir(),key=lambda p:p.name.casefold())
                   if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png')]
            total=len(files)
            self.q.put(('status',f'Indexing JPEGs: 0/{total} · calculating fingerprints…',None))
            # Show at most about 50 progress steps to avoid overloading the UI.
            interval=max(1,(total+49)//50)
            for n,p in enumerate(files,1):
                try:
                    inf=old.photo_info(p);DB.upsert_local(p);found[str(p)]=inf
                except Exception as exc:
                    errors.append((p.name,str(exc)))
                if n==1 or n==total or n%interval==0:
                    self.q.put(('status',f'Indexing JPEGs: {n}/{total} · {len(errors)} error(s) · {p.name}',None))
            self.q.put(('status',f'Indexing {total}/{total} completed · consolidating history…',None))
            DB.import_old_logs(old.EVENTS)
            return found,errors

        def done(result):
            found,errors=result;self.local_items=found
            self.local_tree.delete(*self.local_tree.get_children())
            source_ids=DB.source_ids()
            for key,inf in found.items():
                self.local_tree.insert('','end',iid=key,values=self._local_row(key,inf,source_ids))
            self.mark_local_presence()
            self._sort_tree('local')
            self.status.set(f'Folder {source_dir.name}: {len(found)} files indexed · {len(errors)} error(s) · fingerprints computed')
            old.audit('folder_opened',folder=str(source_dir),count=len(found),errors=len(errors))
        self.background(task,done)

    def mark_local_presence(self):
        with DB.lock:
            paths={r['local_path'] for r in DB.db.execute('SELECT local_path FROM tv_photos WHERE present=1 AND local_path IS NOT NULL')}
        for p in self.local_tree.get_children():
            vals=list(self.local_tree.item(p,'values'))
            vals[3]='Yes' if str(Path(p).resolve()) in paths else '—'
            self.local_tree.item(p,values=vals)

    def show_selected(self,side):
        self.last_selection_side=side
        if side=='local':
            keys=self.local_tree.selection();self.local_ref=None;self.local_preview.configure(image='',text='')
            if len(keys)!=1:
                self.local_detail.configure(text=(f'{len(keys)} photos selected; details hidden.' if keys else 'Select a photo.'));return
            info=self.local_items[keys[0]];db=DB.get_local(keys[0]);
            self.local_detail.configure(text=f"{(db or {}).get('source_id') or '—'} · {info['name']}\n{info['width']} × {info['height']} · {info['bytes']/1e6:.2f} {'MB'}\n16:9 : {'Yes' if info['ratio_ok'] else 'No'} · JPEG 1920×1080 : {'Yes' if info['exact'] and info['format']=='JPEG' else 'No'}\nSHA-256 : {(db or {}).get('sha256','?')[:16]}…\npHash : {(db or {}).get('phash','?')}")
            self._image(self.local_preview,keys[0],'local_ref')
            return
        keys=self.tv_tree.selection()
        self._populate_editor(keys)
        self.tv_ref=None;self.tv_preview.configure(image='',text='')
        if len(keys)!=1:
            description=(f'{len(keys)} photos selected — common settings in dropdowns.'
                         if keys else 'Select a photo.')
            self.tv_detail.configure(text=description)
            self.tv_preview_status.configure(text='Thumbnail: —')
            return
        cid=keys[0];obj=self.tv_items[cid];rec=DB.get_tv(cid) or {}
        source_bytes=None
        if rec.get('local_path'):
            local=DB.get_local(rec['local_path'])
            source_bytes=(local or {}).get('bytes')
        _,jpeg_detail=tv_jpeg_info(rec,source_bytes,self.lang)
        self.tv_detail.configure(text=(
            f"{cid} · {obj.get('width')}×{obj.get('height')}\n"
            +jpeg_detail+'\n'+'Source: '
            +(Path(rec['local_path']).name if rec.get('local_path') else 'match not established')))
        self.tv_preview_status.configure(text='Thumbnail: loading…')
        self.tv_preview.configure(text='Loading thumbnail…')
        def work():
            try:
                p=old.tv_thumbnail(cid,obj)
                if not rec.get('local_path'):
                    match,method,dist=DB.match_thumb(p)
                    if match:DB.link(cid,match,method,dist)
                self.q.put(('tvpreview', (cid,p),None))
            except Exception as exc:self.q.put(('tvpreview_error',(cid,str(exc)),None))
        threading.Thread(target=work,daemon=True).start()

    def _image(self,widget,path,field):
        try:
            from PIL import ImageTk,ImageOps
            with Image.open(path) as im:
                im=ImageOps.exif_transpose(im);im.thumbnail((320,150),Image.Resampling.LANCZOS)
                pic=ImageTk.PhotoImage(im.copy())
            setattr(self,field,pic);widget.configure(image=pic,text='')
            return True
        except Exception as exc:
            widget.configure(image='',text='Thumbnail unavailable')
            logging.warning('image %s: %s',path,exc)
            return False

    def poll(self):
        try:
            while True:
                typ,value,callback=self.q.get_nowait()
                if typ=='ok':
                    if callback:callback(value)
                elif typ=='error':
                    self.status.set('ERROR: '+value)
                    messagebox.showerror('Operation not confirmed',value+'\nCheck your TV before retrying.')
                elif typ=='status':self.status.set(value)
                elif typ=='tvpreview':
                    cid,p=value
                    if self.tv_tree.selection()==(cid,):
                        ok=self._image(self.tv_preview,p,'tv_ref')
                        self.tv_preview_status.configure(
                            text='Thumbnail: displayed' if ok else 'Thumbnail: unavailable')
                elif typ=='tvpreview_error':
                    cid,error=value
                    if self.tv_tree.selection()==(cid,):
                        self.tv_preview.configure(image='',text='Thumbnail unavailable')
                        self.tv_preview_status.configure(text='Thumbnail: unavailable')
                        logging.warning('TV miniature %s : %s',cid,error)
                if typ in ('ok','error'):
                    self.busy=False;self.upload_btn.state(['!disabled']);self.delete_btn.state(['!disabled'])
                    if self._batch_running:self._batch_end()
                    self._update_apply_button()
        except queue.Empty:pass
        self.after(100,self.poll)

    def change_selected(self):
        ids=list(self.tv_tree.selection())
        if not ids:return
        # Simply selecting a photo must never send a TV command.
        if not any(self._field_changes().values()):return
        try:plans=self._desired_updates(ids)
        except ValueError as exc:
            return messagebox.showwarning('Settings',str(exc))
        if not plans:
            self._update_apply_button()
            return messagebox.showinfo('No change',
                'The selected photos already have the requested settings.')
        if len(ids)>1:
            with DB.lock:
                successes=DB.db.execute("SELECT COUNT(*) FROM operations WHERE action='change_settings' AND status IN ('confirmed','partial')").fetchone()[0]
            if not successes:
                return messagebox.showwarning('Single-photo test required',
                    'Verify one single-photo modification before batch editing.')
        count=len(plans)
        if not messagebox.askyesno('Confirm changes',
            f'Apply changed settings to {count} of {len(ids)} selected photo(s)?\nOther settings will stay unchanged.\nArt effects cannot be read back from Samsung.'):return
        def work():
            succeeded=[];cancelled=False
            for n,(cid,matte,filt) in enumerate(plans,1):
                if self._check_stop_between_photos('change_settings',len(succeeded),count-n+1):
                    cancelled=True;break
                self.q.put(('status',f'Editing {n}/{count}: {cid}',None))
                try:
                    result=tv_change(cid,matte,filter_id=filt)
                    if filt is not None:DB.filter_set(cid,filt)
                    DB.operation('change_settings',cid=cid,status='confirmed' if filt is None else 'partial',detail={'requested_matte':matte,'filter_id':filt,'result':result})
                    succeeded.append(cid)
                except Exception as exc:
                    DB.operation('change_settings',cid=cid,status='error',detail=str(exc))
                    raise RuntimeError(f'{len(succeeded)} changes completed; stopped at {cid}: {exc}')
            return len(succeeded),cancelled,_tv_read_enriched()
        def done(result):
            count_done,cancelled,library=result
            self.update_tv(library)
            old.audit('batch_change_summary',processed=count_done,requested=count,cancelled=cancelled)
            self.status.set(f'Editing: {count_done}/{count} photos' + (' — stopped' if cancelled else ''))
            messagebox.showinfo('Done',f'{count_done}/{count} photo(s) processed.' + (' Stopped between photos.' if cancelled else '') + ' Mat read back; effect cannot be independently verified.')
        if not self._begin_batch('change_settings'):return
        self.background(work,done)
        self._update_apply_button()







    def request_batch_stop(self):
        """Request a voluntary batch stop after the current photo is safely processed."""
        if not self._batch_running:return
        self._stop_batch.set()
        self.stop_batch_btn.state(['disabled'])
        self.status.set('Stop requested: finishing the current photo safely…')

    def upload_selected(self):
        keys=list(self.local_tree.selection())
        if not keys:return messagebox.showinfo('Selection','Select files to import from the right.')
        infos=[self.local_items[k] for k in keys]
        try:
            for info in infos:old.verify_upload(info)
        except Exception as exc:return messagebox.showerror('Incompatible JPEG',str(exc))
        quality=LEVELS[self.quality_canonical(self.quality.get())];force=self.force.get()
        known=[];unknown=[]
        for info in infos:
            with DB.lock:
                row=DB.db.execute('SELECT content_id FROM tv_photos WHERE local_path=? AND present=1',(str(Path(info['path']).resolve()),)).fetchone()
            if row:known.append(info['name'])
            else:unknown.append(info)
        if known:
            if not unknown:return messagebox.showinfo('Already on TV',f'{len(known)} matching photos are already on the TV. No duplicates created.')
            messagebox.showinfo('Duplicates avoided',f'{len(known)} photo(s) already on the TV will be skipped.')
        # The legacy storage check is useful, but the Art quota may differ from system storage.
        if not self.check_storage(upload_infos=unknown):return
        if not messagebox.askyesno('Confirm import',
            f"Import {len(unknown)} JPEG(s) · optimization: {self.quality.get()} · force neutral: {('yes' if force else 'no')}?\nStop immediately on Samsung -11 error."):return
        def work():
            completed=[];cancelled=False
            for n,info in enumerate(unknown,1):
                if self._check_stop_between_photos('upload',len(completed),len(unknown)-n+1):
                    cancelled=True;break
                p=Path(info['path']);self.q.put(('status',f'Uploading {n}/{len(unknown)}: {p.name}',None))
                try:
                    matte='none';filt='None' if force else None
                    if not force:
                        with DB.lock:
                            row=DB.db.execute('SELECT matte_id,filter_id FROM tv_photos WHERE local_path=? ORDER BY seen_at DESC LIMIT 1',(str(p.resolve()),)).fetchone()
                        if row:
                            matte=row['matte_id'] or 'none';filt=row['filter_id']
                    sendpath=optimize(p,quality,old.DATA) if quality else p
                    if quality:DB.add_version(sendpath,p,quality)
                    inf=old.photo_info(sendpath)
                    cid,obj=_upload_one(inf,matte,filt)
                    # After verification, update TV state before committing the source link.
                    DB.record_tv_photo(cid,obj)
                    DB.link(cid,p)
                    DB.record_uploaded_version(cid,sendpath,quality)
                    DB.operation('upload',p,cid,'confirmed',{'quality':quality,'file_sha256':sha(sendpath),'matte':matte})
                    completed.append((p.name,cid))
                    if filt is not None:
                        try:
                            tv_change(cid,filter_id=filt)
                            DB.filter_set(cid,filt)
                            DB.operation('filter',p,cid,'sent_unverifiable',{'filter':filt})
                        except Exception as ef:
                            DB.operation('filter',p,cid,'not_confirmed',{'filter':filt,'error':str(ef)})
                            raise RuntimeError(f'Photo {cid} was uploaded, but its filter was not applied: {ef}')
                except Exception as exc:
                    DB.operation('upload',p,status='error',detail=str(exc))
                    old.audit('batch_interrupted',completed=completed,failed=p.name,error=str(exc))
                    raise RuntimeError(f'{len(completed)} photo(s) verified; stopped at {p.name}: {exc}')
                time.sleep(0.5)
            return completed,cancelled,_tv_read_enriched()
        def done(result):
            completed,cancelled,library=result;self.update_tv(library)
            old.audit('batch_upload_summary',completed=len(completed),planned=len(unknown),cancelled=cancelled)
            self.status.set(f'Import: {len(completed)}/{len(unknown)} photos' + (' — stopped' if cancelled else ''))
            messagebox.showinfo('Import complete',f'{len(completed)}/{len(unknown)} photo(s) uploaded and verified.' + (' Stopped between photos.' if cancelled else ''))
        if not self._begin_batch('upload'):return
        self.background(work,done)

    def delete_selected(self):
        ids=list(self.tv_tree.selection())
        if not ids:return messagebox.showinfo('Selection','Select TV photos.')
        if not messagebox.askyesno('Confirm deletion',f'Permanently delete {len(ids)} photo(s) from the TV?\nWindows JPEG files remain untouched.',icon='warning'):return
        def work():
            done=[];cancelled=False
            for n,cid in enumerate(ids,1):
                if self._check_stop_between_photos('delete',len(done),len(ids)-n+1):
                    cancelled=True;break
                self.q.put(('status',f'Deleting {n}/{len(ids)}: {cid}',None))
                try:
                    old.tv_delete(cid)
                    DB.operation('delete',cid=cid,status='confirmed');done.append(cid)
                except Exception as exc:
                    DB.operation('delete',cid=cid,status='error',detail=str(exc))
                    raise RuntimeError(f'{len(done)} deletions verified; stopped at {cid}: {exc}')
            return done,cancelled,_tv_read_enriched()
        def complete(result):
            done,cancelled,library=result;self.update_tv(library)
            old.audit('batch_delete_summary',completed=len(done),planned=len(ids),cancelled=cancelled)
            self.status.set(f'Deletion: {len(done)}/{len(ids)} photos' + (' — stopped' if cancelled else ''))
            messagebox.showinfo('Deletion',f'{len(done)}/{len(ids)} photo(s) deleted and verified.' + (' Stopped between photos.' if cancelled else ''))
        if not self._begin_batch('delete'):return
        self.background(work,complete)

    def check_storage(self,upload_infos=None):
        if self.lang!='en':
            return super().check_storage(upload_infos=upload_infos)
        if upload_infos is None:
            keys=list(self.local_tree.selection())
            candidates=[self.local_items[k] for k in keys] if keys else list(self.local_items.values())
            infos=[];skipped=0
            for info in candidates:
                try:old.verify_upload(info);infos.append(info)
                except ValueError:skipped+=1
            if not infos:
                messagebox.showinfo('Available space','Choose a folder with compatible 1920 × 1080 JPEG photos.')
                return False
        else:
            infos=list(upload_infos);skipped=0
        total_mb=sum(int(info['bytes']) for info in infos)/1_000_000
        prompt=(f'{len(infos)} photo(s) analyzed: {total_mb:.1f} MB of JPEGs.\n'
                +(f'{skipped} incompatible file(s) excluded.\n' if skipped else '')+
                '\nOn your TV: Settings > Support > Device Care > Manage Storage.\n'
                'Enter the FREE space displayed, including its unit.\n'
                'Examples: 2.4 GB or 850 MB.\n'
                'This value is entered manually; the Art API does not expose it.')
        entered=simpledialog.askstring('Available space — The Frame',prompt,parent=self)
        if entered is None:
            old.audit('storage_preflight_cancelled',count=len(infos),sum_mb=round(total_mb,2))
            return False
        # Accept English and French units, e.g., 2.4 GB.
        text_value=entered.strip().lower().replace('gb','go').replace('mb','mo')
        try:
            free_mb=old.parse_free_space_mb(text_value)
            result=old.estimate_capacity(infos,free_mb)
        except ValueError as exc:
            messagebox.showerror('Invalid value',str(exc));return False
        old.audit('storage_preflight',count=result['count'],sum_mb=round(result['sum_mb'],2),
                  free_mb=result['free_mb'],recommended_mb=round(result['recommended_mb'],2),
                  probably_enough=result['probably_enough'],source='user_manual_tv_display',
                  excluded_incompatible=skipped)
        report=(f"Compatible photos: {result['count']}\n"
                f"Total JPEG size: {result['sum_mb']:.1f} MB\n"
                f"Reported TV free space: {result['free_mb']:.1f} MB\n"
                f"Cautious threshold: {result['recommended_mb']:.1f} MB\n\n"
                'Unofficial threshold = JPEG bytes × 1.5 + 300 MB reserve.\n'
                'Samsung may store extra files and caches.\n'
                'General TV free space can differ from Art photo storage.\n')
        if result['probably_enough']:
            messagebox.showinfo('Space: favorable estimate',report+
                '\nFavorable estimate, but no guarantee of available Art storage. '
                'Each upload will still be verified individually.')
            return True
        messagebox.showwarning('Storage may be insufficient',report+
            '\nInsufficient headroom under our cautious rule. '
            'Reduce the batch or free some space before uploading.')
        return False

    def make_export(self):
        self.background(DB.export,lambda p:messagebox.showinfo('Export CSV','Exports created:\n'+str(p)))

    def make_backup(self):
        self.background(lambda:DB.backup(self.extras),lambda p:messagebox.showinfo('Backup','Database and TV inventory backed up:\n'+str(p)+'\nOriginal JPEGs remain in their source folder.'))


def _read_available_only():
    tv=old.connect()
    try:return tv.available()
    finally:
        try:tv.close()
        except Exception:pass

if __name__=='__main__':
    ManagerV2().mainloop()
