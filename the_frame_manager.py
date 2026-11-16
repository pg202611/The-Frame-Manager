#!/usr/bin/env python3
"""The Frame Manager 3.0 — interface graphique bilingue Windows."""
import json, logging, queue, threading, time, re
from tkinter import simpledialog
from frame_core import Preferences, DEFAULTS, MATTES, COLORS, FILTERS, valid_ip
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from PIL import Image, ImageTk, ImageOps

import frame_core as old
from frame_core import Registry, LEVELS, optimize, sha

DB = Registry(old.DATA)


def _tv_read_enriched():
    """Toutes les lectures sont non destructives; ne jamais supposer les filtres par photo."""
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
                extras[method]={'status':'non disponible','error':str(exc)}
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
    """Change uniquement les paramètres explicitement demandés.

    Important (LS03B 2022 / API 4.3.4.0): ne PAS envoyer de
    portrait_matte_id pour une photo paysage. Dans v2.0, ce champ était
    envoyé à chaque change_matte; un refus Samsung -7 a été constaté.
    Le filtre n'est PAS relisible via l'inventaire Art; ne pas le présenter
    comme confirmé par lecture après avoir reçu l'accusé de réception.
    """
    if matte is None and filter_id is None:
        raise ValueError('Aucune modification demandée')
    tv=old.connect();results={}
    try:
        before=old.photos_unique(tv.available())
        if cid not in before:
            raise RuntimeError(f'{cid} absent de la TV : aucune écriture')
        # La lecture du statut aide au diagnostic d'une erreur -7 sans
        # modifier le mode de fonctionnement du diaporama.
        try:
            mode=tv.get_artmode()
            old.audit('artmode_before_edit',content_id=cid,status=mode)
            if isinstance(mode,str) and mode.lower() not in ('on','true','1'):
                raise RuntimeError('Le mode Art ne semble pas actif; aucune écriture envoyée')
        except RuntimeError:
            raise
        except Exception as exc:
            old.audit('artmode_check_unavailable',content_id=cid,error=str(exc))
        current=before[cid].get('matte_id')
        if matte is not None:
            if current == matte:
                results['matte']='déjà appliqué; aucune commande envoyée'
                old.audit('matte_change_skipped',content_id=cid,matte=matte)
            else:
                old.audit('matte_change_started',content_id=cid,requested=matte,
                          previous=current,portrait_parameter='omitted')
                # La méthode Python accepte portrait_matte (facultatif),
                # PAS une obligation. Préserver portrait_matte_id actuel.
                tv.change_matte(cid,matte)
                for attempt in range(5):
                    if attempt:time.sleep(2)
                    after=old.photos_unique(tv.available())
                    if cid in after and after[cid].get('matte_id')==matte:
                        results['matte']='confirmé par relecture'
                        old.audit('matte_change_verified',content_id=cid,matte=matte,
                                  portrait_after=after[cid].get('portrait_matte_id'))
                        break
                if 'matte' not in results:
                    raise RuntimeError(f'{cid}: passe-partout non confirmé; aucun filtre envoyé')
        if filter_id is not None:
            old.audit('filter_change_started',content_id=cid,filter_id=filter_id)
            response=tv.set_photo_filter(cid,filter_id)
            if response is False:
                raise RuntimeError(f'{cid}: Samsung a retourné False pour set_photo_filter')
            results['filter']='commande acceptée; effet non vérifiable par relecture'
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
            raise RuntimeError('Identifiant non confirmé: ne pas relancer automatiquement')
        if cid in before:
            raise RuntimeError('Identifiant de transfert déjà présent avant l’importation; impossible de confirmer une nouvelle photo')
        valid=False
        for attempt in range(6):
            if attempt:time.sleep(2)
            after=old.photos_unique(tv.available())
            if cid in after:
                if after[cid].get('matte_id')!=matte:
                    raise RuntimeError(f'{cid} présent, mais passe-partout différent: {after[cid].get("matte_id")}')
                valid=True;break
        if not valid:raise RuntimeError(f'{cid} non retrouvé; pas de répétition automatique')
        old.audit('upload_verified',filename=info['name'],content_id=cid,matte=matte,new_id=cid not in before)
        # Le filtre est appliqué APRÈS enregistrement de la correspondance
        # dans SQLite, pour ne jamais perdre le lien en cas d'échec du filtre.
        return cid,after[cid]
    finally:
        try:tv.close()
        except Exception:pass

def jpeg_comparison(source):
    """Prépare des JPEG de comparaison, sans communication avec la TV."""
    source=Path(source)
    if not source.is_file():
        raise FileNotFoundError(f'Fichier source introuvable : {source}')
    info=old.photo_info(source)
    old.verify_upload(info)
    original_bytes=source.stat().st_size
    source_sha=sha(source)
    rows=[{'quality':None,'label':'Original','path':source,'bytes':original_bytes,
           'percent':0.0}]
    for quality in (90,85,80):
        path=optimize(source,quality,old.DATA)
        check=old.photo_info(path)
        old.verify_upload(check)
        DB.add_version(path,source,quality)
        rows.append({'quality':quality,'label':f'JPEG {quality}','path':path,
                     'bytes':path.stat().st_size,
                     'percent':100*(original_bytes-path.stat().st_size)/original_bytes})
    return {'source':source,'source_sha':source_sha,'rows':rows}


def replace_tv_photo(cid,quality,progress=lambda message:None,lang='fr',backup_before=True):
    """Remplacement à deux phases, sans supprimer l'original en cas d'échec.

    1: préparer + téléverser + vérifier + restituer filtre connu ;
    2: supprimer l'ancien ID seulement si toutes les étapes ont réussi.
    L'identifiant Samsung change forcément; aucune écriture directe de ses octets.
    """
    if quality not in (90,85,80,None):
        raise ValueError('Choisissez Aucune, JPEG 90, JPEG 85 ou JPEG 80 pour le remplacement.')
    if not re.fullmatch(r'MY_[A-Za-z0-9_-]+',str(cid)):
        raise ValueError('Seules les photos personnelles MY_ peuvent être remplacées.')
    entry=DB.get_tv(cid)
    if not entry or not entry['present']:
        raise RuntimeError('Photo non présente dans le dernier inventaire : actualisez la TV.')
    if not entry['local_path']:
        raise RuntimeError('Aucune correspondance avec un JPEG source Windows pour cette photo.')
    source=Path(entry['local_path'])
    if not source.is_file():
        raise FileNotFoundError(f'JPEG source indisponible : {source}\nChoisissez le dossier source dans Manager.')
    old.verify_upload(old.photo_info(source))
    sendpath=source if quality is None else optimize(source,quality,old.DATA)
    old.verify_upload(old.photo_info(sendpath))
    source_sha=sha(source)
    new_sha=sha(sendpath)
    new_size=sendpath.stat().st_size
    # Un remplacement volontaire peut augmenter la taille (ex: 80 -> 90
    # ou 80 -> original). On refuse seulement les OCTETS identiques connus.
    prior_sha=entry.get('uploaded_sha256')
    if prior_sha and prior_sha==new_sha:
        raise RuntimeError(f'{cid} : fichier identique à la version TV enregistrée; remplacement évité.')
    # Relecture réelle avant toute écriture pour éviter des ID obsolètes.
    progress(f'Checking TV: {cid}…' if lang=='en' else f'Contrôle TV : {cid}…')
    tv=old.connect()
    try:
        before=old.photos_unique(tv.available())
    finally:
        try:tv.close()
        except Exception:pass
    if cid not in before:
        raise RuntimeError(f'{cid} n’existe plus sur la TV : remplacement annulé.')
    current=before[cid]
    matte=current.get('matte_id') or 'none'
    portrait=current.get('portrait_matte_id') or 'none'
    filter_id=entry['filter_id'] if entry['filter_source'] else None
    # Les effets non connus ne peuvent pas être récupérés depuis Samsung.
    if quality is not None: DB.add_version(sendpath,source,quality)
    backup=DB.backup() if backup_before else None
    DB.operation('replace_photo',source,cid,'préparé',
        {'quality':quality,'new_bytes':new_size,'old_bytes_known':entry.get('uploaded_bytes'),
         'source_sha256':source_sha,'sha256':new_sha,'matte':matte,
         'filter':filter_id,'backup':str(backup)})
    old.audit('replace_started',old_cid=cid,source=source.name,quality=quality,
              new_bytes=new_size,matte=matte,filter_id=filter_id)
    new_id=None
    try:
        label='Original' if quality is None else f'JPEG {quality}'
        progress(f'Uploading {label} (previous {cid} untouched)…' if lang=='en' else f'Transfert {label} (ancien {cid} intact)…')
        new_id,obj=_upload_one(old.photo_info(sendpath),matte,portrait_matte=portrait)
        if new_id==cid or new_id in before:
            raise RuntimeError('Samsung a renvoyé un identifiant déjà existant : aucune suppression autorisée.')
        DB.record_tv_photo(new_id,obj)
        DB.link(new_id,source,method='remplacement après optimisation JPEG')
        DB.record_uploaded_version(new_id,sendpath,quality)
        DB.operation('replace_photo',source,new_id,'nouvelle_importée',
                     {'old_id':cid,'new_id':new_id,'quality':quality})
        if filter_id is not None:
            progress(f'Restoring {filter_id} effect to {new_id}…' if lang=='en' else f'Restauration de l’effet {filter_id} sur {new_id}…')
            tv_change(new_id,filter_id=filter_id)
            DB.filter_set(new_id,filter_id)
            DB.operation('replace_photo',source,new_id,'filtre_envoyé',
                         {'old_id':cid,'filter_id':filter_id,
                          'note':'Effet non relisible individuellement depuis Samsung'})
        # Début de la phase destructive : l'ancienne photo ne sera supprimée
        # que si le transfert et les paramètres connus ont été acceptés.
        progress(f'New photo {new_id} verified; deleting old {cid}…' if lang=='en' else f'Nouvelle photo {new_id} vérifiée ; suppression de {cid}…')
        old.tv_delete(cid)  # vérifie la disparition par relecture de la TV
        DB.mark_replaced(cid,new_id)
        DB.operation('replace_photo',source,new_id,'confirmé',
                     {'old_id':cid,'new_id':new_id,'quality':quality,
                      'size_original':source.stat().st_size,'size_new':new_size,
                      'matte':matte,'filter':filter_id})
        old.audit('replace_verified',old_cid=cid,new_cid=new_id,quality=quality,
                  new_bytes=new_size,matte=matte,filter_id=filter_id)
        return {'old_id':cid,'new_id':new_id,'quality':quality,
                'size_old_source':source.stat().st_size,'size_old_tv':entry.get('uploaded_bytes'),'size_new':new_size,
                'matte':matte,'filter':filter_id}
    except Exception as exc:
        DB.operation('replace_photo',source,new_id or cid,'erreur',
                     {'old_id':cid,'new_id':new_id,'quality':quality,'error':str(exc),
                      'manual_inspection_required':bool(new_id)})
        old.audit('replace_interrupted',old_cid=cid,new_cid=new_id,quality=quality,error=str(exc))
        if new_id:
            raise RuntimeError(f'{exc}\nATTENTION : la nouvelle photo {new_id} peut rester sur la TV avec l’ancienne {cid}. Aucune suppression ni relance automatique. Actualisez pour vérifier.') from exc
        raise RuntimeError(f'{exc}\nAncienne photo {cid} conservée (aucune suppression demandée).') from exc



def prepare_jpeg_batch(ids,quality,progress=lambda current,total,cid:None):
    """Analyse en lecture seule de la TV (via les données SQLite) et des sources.

    Un gain peut être négatif : meilleure qualité, fichier original, etc.
    On évite uniquement les versions dont le SHA256 est déjà identique.
    Les tailles inconnues sur la TV ne sont PAS remplacées par des hypothèses.
    """
    if quality not in (None,90,85,80):
        raise ValueError('Aucune, JPEG 90, JPEG 85 ou JPEG 80 requis.')
    if not ids or len(ids)!=len(set(ids)):
        raise ValueError('Sélection vide ou identifiants répétés.')
    ready=[];skipped=[]
    for n,cid in enumerate(ids,1):
        progress(n,len(ids),cid)
        entry=DB.get_tv(cid)
        reason=None
        if not re.fullmatch(r'MY_[A-Za-z0-9_-]+',str(cid)):
            reason='Identifiant non personnel'
        elif not entry or not entry.get('present'):
            reason='Photo absente du dernier inventaire'
        elif not entry.get('local_path'):
            reason='Aucune correspondance Windows confirmée'
        else:
            source=Path(entry['local_path'])
            if not source.is_file():
                reason='JPEG source introuvable'
            else:
                try:
                    old.verify_upload(old.photo_info(source))
                    target=source if quality is None else optimize(source,quality,old.DATA)
                    old.verify_upload(old.photo_info(target))
                    new_bytes=target.stat().st_size
                    old_bytes=entry.get('uploaded_bytes')
                    new_sha=sha(target)
                    if entry.get('uploaded_sha256')==new_sha:
                        reason='Fichier déjà identique à la version TV enregistrée (SHA-256)'
                    else:
                        ready.append({'cid':cid,'source':str(source),'old_bytes':old_bytes,
                                      'source_bytes':source.stat().st_size,'source_sha256':sha(source),
                                      'new_bytes':new_bytes,'quality':quality,
                                      'filter_known':bool(entry.get('filter_source'))})
                except Exception as exc:
                    reason=f'Préparation impossible : {exc}'
        if reason:
            skipped.append({'cid':cid,'reason':reason})
    known=[item for item in ready if item['old_bytes'] is not None]
    net_variation=sum(item['new_bytes']-item['old_bytes'] for item in known)
    unknown_count=sum(item['old_bytes'] is None for item in ready)
    more_count=sum(item['new_bytes']>item['old_bytes'] for item in known)
    less_count=sum(item['new_bytes']<item['old_bytes'] for item in known)
    benchmark_delta=sum(item['new_bytes']-item['source_bytes'] for item in ready)
    return {'ids':tuple(ids),'quality':quality,'ready':ready,'skipped':skipped,
            'net_variation':net_variation,'benchmark_delta':benchmark_delta,'known_saving':-net_variation,
            'unknown_size_count':unknown_count,'known_count':len(known),
            'increases':more_count,'decreases':less_count,
            'new_total_bytes':sum(item['new_bytes'] for item in ready)}


def run_jpeg_batch(plan,stop_event,progress=lambda i,total,cid:None):
    """Exécute une seule photo à la fois, arrêt à la première anomalie.

    Le drapeau d'arrêt n'est évalué qu'entre les photos : jamais entre
    l'envoi et la suppression sécurisée d'une même photo.
    """
    ids=plan['ready'];quality=plan['quality']
    results=[];failure=None;cancelled=False;backup=None
    try:
        backup=DB.backup()
        DB.operation('batch_replace',status='début',detail={
            'quality':quality,'requested':list(plan['ids']),
            'ready':len(ids),'skipped':plan['skipped'],'backup':str(backup)})
        for i,item in enumerate(ids,1):
            if stop_event.is_set():
                cancelled=True
                break
            cid=item['cid']
            progress(i,len(ids),cid)
            try:
                current=DB.get_tv(cid)
                if not current or not current['present'] or current.get('local_path')!=item['source']:
                    raise RuntimeError('Correspondance TV/Windows modifiée depuis la préparation : arrêt prudent.')
                if sha(item['source'])!=item['source_sha256']:
                    raise RuntimeError('JPEG source modifié depuis la préparation : arrêt prudent.')
                result=replace_tv_photo(cid,quality,backup_before=False)
                results.append(result)
            except Exception as exc:
                failure={'cid':cid,'error':str(exc)}
                break
    except Exception as exc:
        failure={'cid':None,'error':str(exc)}
    finally:
        DB.operation('batch_replace',status=('erreur' if failure else 'interrompu' if cancelled else 'terminé'),detail={
            'quality':quality,'completed':results,'skipped':plan['skipped'],
            'failure':failure,'cancelled':cancelled,'backup':str(backup) if backup else None})
    return {'completed':results,'skipped':plan['skipped'],'failure':failure,
            'cancelled':cancelled,'backup':str(backup) if backup else None,
            'requested':len(plan['ids']),'ready':len(ids)}

def format_size_variation(delta_bytes, lang='fr'):
    """Variation de taille des OCTETS JPEG connus, pas de l'espace libre TV."""
    amount=abs(delta_bytes)/1e6
    if delta_bytes<0:
        return f'Saving {amount:.2f} MB' if lang=='en' else f'Économie de {amount:.2f} Mo'
    if delta_bytes>0:
        return f'Increase of {amount:.2f} MB' if lang=='en' else f'Augmentation de {amount:.2f} Mo'
    return 'No size change (0.00 MB)' if lang=='en' else 'Variation nulle (0,00 Mo)'


def tv_jpeg_info(rec, source_bytes=None, lang='fr'):
    """Libellés JPEG fondés uniquement sur la version réellement consignée dans SQLite.

    quality=None avant le suivi des transferts est 'inconnu', et non 'sans
    optimisation'. Quand record_uploaded_version() a été appelé avec
    quality=None, uploaded_sha256 et uploaded_bytes existent : c'est l'envoi
    du fichier source, sans optimisation par Manager.
    """
    rec=rec or {}
    qual=rec.get('uploaded_quality')
    sent_sha=rec.get('uploaded_sha256')
    sent_bytes=rec.get('uploaded_bytes')
    if qual in (80,85,90):
        label=f'JPEG {qual}'
        column=str(qual)
    elif sent_sha and isinstance(sent_bytes,int) and sent_bytes>0:
        label='None (uploaded without Manager optimization)' if lang=='en' else 'Aucune (envoi sans optimisation Manager)'
        column='None' if lang=='en' else 'Aucune'
    else:
        return '—', ('JPEG optimization: unknown (earlier upload not documented)' if lang=='en' else 'Optimisation JPEG : inconnue (ancien transfert non documenté)')
    detail=f'{"JPEG optimization" if lang=="en" else "Optimisation JPEG"} : {label}'
    if isinstance(sent_bytes,int) and sent_bytes>0:
        detail+=f' · {sent_bytes/1e6:.2f} Mo'
        if qual in (80,85,90) and isinstance(source_bytes,int) and source_bytes>0:
            saving=(1-sent_bytes/source_bytes)*100
            detail+=f' · {saving:.1f}% smaller than source' if lang=='en' else f' · {saving:.1f} % de moins que le source'
    return column,detail


def split_matte_id(matte):
    """Décoder les IDs Samsung sans inventer de couleur quand matte=none.

    Une réponse absente ou non reconnue reste inconnue; ne jamais la
    transformer silencieusement en aucun passe-partout.
    """
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
        # Chargement AVANT le constructeur v1.2 : celui-ci actualise la TV au démarrage.
        self.prefs_store=Preferences(old.DATA)
        self.prefs=dict(self.prefs_store.data)
        self.lang=self.prefs['language']
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
        # Sous cette taille, les cadres fixes empiètent sur les listes.
        self.minsize(920,680)
        # Le redimensionnement doit rester explicitement actif sur Windows.
        # La taille minimale protege les commandes fixes du panneau inferieur.
        self.resizable(width=True, height=True)
        self.title(self.T('The Frame Manager 3.0 — Samsung The Frame',
                          'The Frame Manager 3.0 — Samsung The Frame'))

    def quit_app(self):
        # Sauvegarder également un glissement tout juste terminé avant de fermer.
        if hasattr(self,'tv_tree'):
            self._capture_column_widths('tv')
        if hasattr(self,'local_tree'):
            self._capture_column_widths('local')
        super().quit_app()

    def T(self,fr,en):
        return en if self.lang=='en' else fr

    def unchanged(self):
        return self.T('(ne pas modifier)','(leave unchanged)')

    def quality_choices(self):
        return [self.T('Aucune','None'),'JPEG 90','JPEG 85','JPEG 80']

    def quality_canonical(self,value):
        return 'Aucune' if value in ('Aucune','None') else value

    def current_default(self,key):
        value=self.prefs[key]
        return self.unchanged() if value=='(ne pas modifier)' else (self.T('Aucune','None') if value=='Aucune' else value)

    def unknown(self):
        return self.T('(inconnu)','(unknown)')

    def multiple(self):
        return self.T('(valeurs multiples)','(multiple values)')

    def no_color(self):
        return self.T('(sans couleur)','(no color)')

    def _apply_option_values(self):
        """Met à jour les choix sans effacer les valeurs agrégées affichées."""
        styles=[self.unchanged(),self.unknown(),self.multiple()]+list(dict.fromkeys(list(self.matte_types)+list(MATTES[1:])))
        colors=[self.no_color(),self.multiple()]+list(dict.fromkeys(list(self.matte_colors)+list(COLORS)))
        filters=[self.unchanged(),self.unknown(),self.multiple()]+list(dict.fromkeys(list(self.filter_types)+list(FILTERS[1:])))
        self.style_combo.configure(values=list(dict.fromkeys(styles)))
        self.color_combo.configure(values=list(dict.fromkeys(colors)))
        self.filter_combo.configure(values=list(dict.fromkeys(filters)))

    def _initial_editor_values(self, ids):
        """Les lectures TV sont prioritaires; un filtre sans source est inconnu."""
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
        """L'état affiché ne déclenche aucune modification par lui-même."""
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
        """Différentiel par photo : préserver les champs non modifiés.

        Les photos sans passe-partout restent telles quelles si seule la
        couleur change. Ne pas supposer un filtre inconnu = None.
        """
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
                    raise ValueError(self.T('Passe-partout actuel inconnu; choisissez explicitement un type.',
                                            'Current mat style unknown; select a style explicitly.'))
            matte=None
            if delta['style'] or delta['color']:
                if style=='none':target='none'
                elif style is not None:
                    if delta['color']:color=self.matte_color.get()
                    elif old_color in COLORS:color=old_color
                    else:color=self.prefs['matte_color']
                    if color not in COLORS:
                        raise ValueError(self.T('Couleur de passe-partout invalide.','Invalid mat color.'))
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
            # Ne pas étirer chaque colonne à la largeur de la fenêtre :
            # Treeview doit pouvoir faire défiler horizontalement le tableau.
            tree.column(col,width=int(width),minwidth=50,stretch=False,anchor='w')

    def make_tree(self,parent,columns):
        """Deux barres de défilement, toujours accolées à la zone visible."""
        # Le cadre 'parent' est géré par grid par le panneau principal.
        # Ne pas mêler pack et grid dans cet objet.
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
        # Tk finalise le glissement des séparateurs après ButtonRelease.
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
            logging.warning('Sauvegarde largeurs colonnes %s : %s',side,exc)

    def _schedule_column_width_save(self,side):
        # Déclenché aussi par des clics de tri sans changer de largeur :
        # _capture ne sauvegarde que lorsque la largeur a réellement changé.
        self.after(120,lambda:self._capture_column_widths(side))

    def _begin_batch(self,kind):
        """Activer l'arrêt volontaire d'un lot, sur le thread Tk uniquement."""
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
        """Tri numérique des IDs et qualités; tri naturel des noms de fichiers."""
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
            # IDs SRC-00012 et MY_F0428 doivent être triés numériquement.
            if (side=='tv' and index in (0,1)) or (side=='local' and index==0):
                m=re.search(r'(\d+)$',v)
                return (0,0,int(m.group(1)) if m else -1)
            if (side=='tv' and index==4):
                numeric={'Aucune':0,'None':0}
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
        # Affiche le sens du tri par une flèche dans l'en-tête.
        cols=tree['columns']
        for i,col in enumerate(cols):
            label=col+(' ▼' if reverse else ' ▲') if index==i else col
            tree.heading(col,text=label)

    def _restore_lists(self,selected_tv=(),selected_local=()):
        # Recrée les listes sans écrire dans SQLite.
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
        if self.busy:raise RuntimeError(self.T('Opération en cours','Operation in progress'))
        tvsel=self.tv_tree.selection();locsel=self.local_tree.selection()
        self._capture_column_widths('tv');self._capture_column_widths('local')
        for child in list(self.winfo_children()):
            if child is not self.settings_window:child.destroy()
        self.build()
        self._restore_lists(tvsel,locsel)
        self._populate_editor(self.tv_tree.selection())
        self.status.set(self.T('Paramètres appliqués.','Settings applied.'))
        self.title(self.T('The Frame Manager 3.0 — Samsung The Frame',
                          'The Frame Manager 3.0 — Samsung The Frame'))

    def open_settings(self):
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.lift();self.settings_window.focus_set();return
        win=tk.Toplevel(self);self.settings_window=win
        win.title(self.T('Paramètres du logiciel','Application settings'))
        win.geometry('510x565');win.minsize(470,520)
        win.transient(self);win.grab_set()
        outer=ttk.Frame(win,padding=15);outer.pack(fill='both',expand=True)
        ttk.Label(outer,text=self.T('Connexion et interface','Connection and interface'),
                  font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(0,10))
        def field(label,value,choices=None):
            box=ttk.Frame(outer);box.pack(fill='x',pady=5)
            ttk.Label(box,text=label,width=27).pack(side='left')
            var=tk.StringVar(value=value)
            if choices is None:w=ttk.Entry(box,textvariable=var)
            else:w=ttk.Combobox(box,textvariable=var,values=choices,state='readonly')
            w.pack(side='left',fill='x',expand=True)
            return var
        ip=field(self.T('Adresse IP du téléviseur','TV IP address'),self.prefs['ip'])
        language=field(self.T('Langue / Language','Language / Langue'),
                       'Français' if self.lang=='fr' else 'English',['Français','English'])
        ttk.Separator(outer).pack(fill='x',pady=12)
        ttk.Label(outer,text=self.T('Configurations par défaut','Default configurations'),
                  font=('Segoe UI',11,'bold')).pack(anchor='w',pady=(0,8))
        quality=field(self.T('Optimisation JPEG','JPEG optimization'),
                      self.current_default('jpeg_quality'),
                      [self.T('Aucune','None'),'JPEG 90','JPEG 85','JPEG 80'])
        force=tk.BooleanVar(value=self.prefs['force_neutral'])
        ttk.Checkbutton(outer,text=self.T('Forcer sans passe-partout et sans effet à l’importation',
                                          'Force no mat and no art effect when importing'),
                        variable=force).pack(anchor='w',pady=(5,7))
        matte=field(self.T('Passe-partout à présélectionner','Default mat style'),
                    self.current_default('matte_style'),[self.unchanged()]+list(MATTES[1:]))
        color=field(self.T('Couleur du passe-partout','Default mat color'),
                    self.prefs['matte_color'],list(COLORS))
        effect=field(self.T('Effet artistique à présélectionner','Default art effect'),
                     self.current_default('art_filter'),[self.unchanged()]+list(FILTERS[1:]))
        ttk.Label(outer,text=self.T(
            'Les valeurs par défaut présélectionnent les menus; aucune modification n’est envoyée à la TV automatiquement.',
            'Defaults preselect menu values; no changes are sent to the TV automatically.'),
                  wraplength=470,foreground='#666',justify='left').pack(anchor='w',pady=(13,5))
        buttons=ttk.Frame(outer);buttons.pack(side='bottom',fill='x',pady=(12,0))
        def close():
            win.grab_release();win.destroy();self.settings_window=None
        def save():
            if self.busy:
                messagebox.showwarning(self.T('Opération en cours','Operation in progress'),
                    self.T('Attendez la fin de l’opération avant de modifier les paramètres.',
                           'Wait until the operation finishes before changing settings.'),parent=win)
                return
            try:
                new_ip=valid_ip(ip.get())
            except ValueError:
                messagebox.showerror(self.T('Adresse IP invalide','Invalid IP address'),
                    self.T('Entrez une adresse IPv4 valide, comme 192.168.1.100, sans http:// ni port.',
                           'Enter a valid IPv4 address, such as 192.168.1.100, without http:// or a port.'),parent=win)
                return
            new={'ip':new_ip,'language':'en' if language.get()=='English' else 'fr',
                 'jpeg_quality':self.quality_canonical(quality.get()),
                 'force_neutral':force.get(),
                 'matte_style':'(ne pas modifier)' if matte.get()==self.unchanged() else matte.get(),
                 'matte_color':color.get(),
                 'art_filter':'(ne pas modifier)' if effect.get()==self.unchanged() else effect.get()}
            try:stored=self.prefs_store.save(new)
            except Exception as exc:
                messagebox.showerror(self.T('Erreur d’enregistrement','Save error'),str(exc),parent=win)
                return
            old_ip=old.IP
            changed_lang=self.lang!=stored['language']
            self.prefs=stored;self.lang=stored['language'];old.IP=stored['ip']
            close()
            if changed_lang:
                self._rebuild_interface()
            else:
                self.quality.set(self.current_default('jpeg_quality'))
                self.force.set(stored['force_neutral'])
                self._populate_editor(self.tv_tree.selection())
            self.ip_label.configure(text=f'   {old.MODEL}  ·  {old.IP}')
            if old_ip!=old.IP:
                # Garder l'ancienne base historique mais effacer la liste TV
                # en attendant la relecture du nouvel appareil.
                self.tv_tree.delete(*self.tv_tree.get_children());self.tv_items={}
                self.status.set(self.T('Adresse IP mise à jour — reconnexion à la TV…',
                                       'IP address updated — reconnecting to the TV…'))
                self.refresh_tv()
            else:
                self.status.set(self.T('Paramètres enregistrés.','Settings saved.'))
        ttk.Button(buttons,text=self.T('Annuler','Cancel'),command=close).pack(side='right',padx=(8,0))
        ttk.Button(buttons,text=self.T('Enregistrer','Save'),command=save).pack(side='right')
        win.protocol('WM_DELETE_WINDOW',close)
        win.focus_set()

    def build(self):
        style=ttk.Style(self)
        if 'vista' in style.theme_names():style.theme_use('vista')
        top=ttk.Frame(self,padding=9);top.pack(fill='x')
        ttk.Label(top,text='THE FRAME MANAGER 3.0',font=('Segoe UI',16,'bold')).pack(side='left')
        self.ip_label=ttk.Label(top,text=f'   {old.MODEL}  ·  {old.IP}',foreground='#666');self.ip_label.pack(side='left')
        ttk.Button(top,text=self.T('Paramètres…','Settings…'),command=self.open_settings).pack(side='right',padx=4)
        ttk.Button(top,text=self.T('Sauvegarder','Backup'),command=self.make_backup).pack(side='right',padx=4)
        ttk.Button(top,text=self.T('Exporter CSV','Export CSV'),command=self.make_export).pack(side='right',padx=4)
        ttk.Button(top,text=self.T('Actualiser TV','Refresh TV'),command=self.refresh_tv).pack(side='right',padx=4)
        # IMPORTANT : réserver le pied de fenêtre AVANT le panneau extensible.
        # Dans v2.0.4, le Label de statut était pack() en dernier ; sous
        # Windows, la zone principale prenait toute la hauteur et le statut
        # n'était même plus mappé (reproduit à 1240x780 sous Tk 8.6).
        # Un pack(side='bottom') avant le pane garantit sa visibilité.
        # Bandeau du bas : statut extensible + poignée de taille visible.
        # Sizegrip autorise le glisser depuis le coin inferieur droit,
        # meme si le theme Windows affiche un rebord de fenetre tres fin.
        # Important: packer le bandeau AVANT le panneau principal extensible.
        self.status_footer=ttk.Frame(self)
        self.status_footer.pack(side='bottom',fill='x')
        self.status_bar=ttk.Label(self.status_footer,textvariable=self.status,
                                  relief='sunken',anchor='w',padding=6)
        self.status_bar.pack(side='left',fill='x',expand=True)
        self.resize_grip=ttk.Sizegrip(self.status_footer)
        self.resize_grip.pack(side='right',anchor='se',padx=(3,2),pady=(0,2))
        pane=ttk.Frame(self,padding=(9,3));pane.pack(fill='both',expand=True)
        pane.columnconfigure(0,weight=1);pane.columnconfigure(2,weight=1);pane.rowconfigure(0,weight=1)
        left=ttk.LabelFrame(pane,text=self.T('Photos sur The Frame','Photos on The Frame'),padding=7);left.grid(row=0,column=0,sticky='nsew')
        mid=ttk.Frame(pane,padding=7);mid.grid(row=0,column=1,sticky='ns')
        right=ttk.LabelFrame(pane,text=self.T('JPEG locaux — sources non modifiées','Local JPEGs — original files untouched'),padding=7);right.grid(row=0,column=2,sticky='nsew')
        # Les listes occupent les rangées extensibles; les détails restent
        # en bas à hauteur presque fixe lors du redimensionnement.
        left.columnconfigure(0,weight=1);left.rowconfigure(0,weight=1)
        right.columnconfigure(0,weight=1);right.rowconfigure(0,weight=1)
        tv_list_host=ttk.Frame(left);tv_list_host.grid(row=0,column=0,sticky='nsew')
        local_list_host=ttk.Frame(right);local_list_host.grid(row=0,column=0,sticky='nsew')
        self.tv_tree=self.make_tree(tv_list_host,(self.T('ID TV','TV ID'),self.T('ID source','Source ID'),self.T('Dimensions','Dimensions'),self.T('Passe-partout','Mat style'),'JPEG'))
        self._tree_column_widths(self.tv_tree,self.prefs_store.data['column_widths']['tv'])
        self.tv_tree.bind('<ButtonRelease-1>',lambda e:self._schedule_column_width_save('tv'),add='+')
        self._enable_column_sort(self.tv_tree,'tv')
        # Le tableau a une hauteur minimale compacte (6 lignes) mais reste
        # extensible. À 1240x780, la zone de détails et ses boutons restent
        # ainsi tous visibles au-dessus de la barre de statut.
        self.tv_tree.configure(height=6)
        self.local_tree=self.make_tree(local_list_host,(self.T('ID source','Source ID'),self.T('Fichier','File'),self.T('Dimensions','Dimensions'),'TV'))
        self._tree_column_widths(self.local_tree,self.prefs_store.data['column_widths']['local'])
        self.local_tree.bind('<ButtonRelease-1>',lambda e:self._schedule_column_width_save('local'),add='+')
        self._enable_column_sort(self.local_tree,'local')
        self.local_tree.configure(height=6)
        self.tv_tree.bind('<<TreeviewSelect>>',lambda _ :self.show_selected('tv'))
        self.local_tree.bind('<<TreeviewSelect>>',lambda _ :self.show_selected('local'))
        ttk.Button(right,text=self.T('Choisir un dossier source…','Select source folder…'),command=self.choose_folder).grid(row=1,column=0,sticky='ew',pady=6)
        ttk.Label(mid,text=self.T('Transfert','Transfer'),font=('Segoe UI',10,'bold')).pack(pady=(32,10))
        ttk.Label(mid,text=self.T('Optimisation JPEG','JPEG optimization')).pack(anchor='w')
        self.quality=tk.StringVar(value=self.current_default('jpeg_quality'))
        ttk.Combobox(mid,width=16,values=self.quality_choices(),state='readonly',textvariable=self.quality).pack(fill='x',pady=3)
        self.force=tk.BooleanVar(value=self.prefs['force_neutral'])
        ttk.Checkbutton(mid,text=self.T('Forcer sans bordure\net sans effet (imports)','Force no mat\nand no effect (imports)'),variable=self.force).pack(pady=(4,8))
        self.upload_btn=ttk.Button(mid,text=self.T('← Importer','← Import'),command=self.upload_selected);self.upload_btn.pack(fill='x',pady=4)
        self.delete_btn=ttk.Button(mid,text=self.T('✕ Supprimer','✕ Delete'),command=self.delete_selected);self.delete_btn.pack(fill='x',pady=4)
        ttk.Separator(mid,orient='horizontal').pack(fill='x',pady=(13,8))
        self.jpeg_compare_btn=ttk.Button(mid,text=self.T('Comparer les JPEG…','Compare JPEGs…'),command=self.compare_selected)
        self.jpeg_compare_btn.pack(fill='x',pady=3)
        self.jpeg_replace_btn=ttk.Button(mid,text=self.T('↻ Remplacer JPEG','↻ Replace JPEGs'),command=self.replace_selected_tv)
        self.jpeg_replace_btn.pack(fill='x',pady=3)
        ttk.Label(mid,text=self.T('Sélection multiple autorisée\nTraitement séquentiel','Multi-selection supported\nSequential processing'),foreground='#666',justify='center').pack(pady=(0,2))
        self.stop_batch_btn=ttk.Button(mid,text=self.T('Arrêter après cette photo','Stop after current photo'),command=self.stop_jpeg_batch)
        self.stop_batch_btn.pack(fill='x',pady=(2,4))
        self.stop_batch_btn.state(['disabled'])

        ttk.Label(mid,text=self.T('Ctrl / Maj :\nsélection multiple','Ctrl / Shift:\nmultiple selection'),justify='center').pack(pady=13)
        edit=ttk.LabelFrame(left,text=self.T('Détails TV et édition des paramètres','TV details and settings editor'),padding=7)
        edit.grid(row=1,column=0,sticky='ew',pady=(8,0))
        # La zone d'image conserve exactement la même hauteur pendant le
        # chargement et après l'affichage d'une miniature (jusqu'à 320×150).
        # pack_propagate(False) empêche l'image de redimensionner ce panneau.
        self.tv_preview_slot=ttk.Frame(edit,width=340,height=168)
        self.tv_preview_slot.pack(fill='x')
        self.tv_preview_slot.pack_propagate(False)
        self.tv_preview=ttk.Label(self.tv_preview_slot,text=self.T('Miniature TV','TV thumbnail'),anchor='center')
        self.tv_preview.pack(fill='both',expand=True)
        # Trois lignes utiles, même hauteur pour chaque sélection.
        self.tv_detail_slot=ttk.Frame(edit,height=63)
        self.tv_detail_slot.pack(fill='x',pady=(5,0))
        self.tv_detail_slot.pack_propagate(False)
        self.tv_detail=ttk.Label(self.tv_detail_slot,text=self.T('Sélectionnez une photo.','Select a photo.'),justify='left',anchor='nw')
        self.tv_detail.pack(fill='both',expand=True)
        self.tv_preview_status=ttk.Label(edit,text=self.T('Miniature : —','Thumbnail: —'),foreground='#666')
        self.tv_preview_status.pack(anchor='w',pady=(0,5))
        self.matte_style=tk.StringVar(value=self.current_default('matte_style'))
        self.matte_color=tk.StringVar(value=self.current_default('matte_color'))
        self.filter_setting=tk.StringVar(value=self.current_default('art_filter'))
        params=ttk.Frame(edit);params.pack(fill='x');params.columnconfigure(1,weight=1)
        ttk.Label(params,text=self.T('Passe-partout','Mat style')).grid(row=0,column=0,sticky='w')
        self.style_combo=ttk.Combobox(params,textvariable=self.matte_style,state='readonly',values=[self.unchanged(),'none']);self.style_combo.grid(row=0,column=1,sticky='ew')
        ttk.Label(params,text=self.T('Couleur','Color')).grid(row=1,column=0,sticky='w')
        self.color_combo=ttk.Combobox(params,textvariable=self.matte_color,state='readonly',values=['polar']);self.color_combo.grid(row=1,column=1,sticky='ew')
        ttk.Label(params,text=self.T('Filtre','Art effect')).grid(row=2,column=0,sticky='w')
        self.filter_combo=ttk.Combobox(params,textvariable=self.filter_setting,state='readonly',values=[self.unchanged(),'None']);self.filter_combo.grid(row=2,column=1,sticky='ew')
        self.apply_btn=ttk.Button(edit,text=self.T('Appliquer aux photos sélectionnées','Apply to selected photos'),command=self.change_selected)
        self.apply_btn.pack(fill='x',pady=(6,1))
        self.apply_btn.state(['disabled'])
        for var in (self.matte_style,self.matte_color,self.filter_setting):
            var.trace_add('write',self._on_editor_change)
        ttk.Label(edit,text=self.T('Seuls les réglages choisis changent.\nLecture du filtre par photo non garantie.','Only selected settings are changed.\nPer-photo effect cannot be read back.'),foreground='#666').pack(anchor='w')
        local=ttk.LabelFrame(right,text=self.T('Détails du fichier local','Local file details'),padding=7)
        local.grid(row=2,column=0,sticky='ew',pady=(8,0))
        self.local_preview_slot=ttk.Frame(local,height=168)
        self.local_preview_slot.pack(fill='x')
        self.local_preview_slot.pack_propagate(False)
        self.local_preview=ttk.Label(self.local_preview_slot,text=self.T('Miniature Windows','Windows thumbnail'),anchor='center')
        self.local_preview.pack(fill='both',expand=True)
        self.local_detail_slot=ttk.Frame(local,height=88)
        self.local_detail_slot.pack(fill='x',pady=4)
        self.local_detail_slot.pack_propagate(False)
        self.local_detail=ttk.Label(self.local_detail_slot,text=self.T('Sélectionnez une photo.','Select a photo.'),justify='left',anchor='nw')
        self.local_detail.pack(fill='both',expand=True)
        self.tv_ref=None;self.local_ref=None
        self._populate_editor(())
        # Barre de statut déjà installée juste sous la barre de titre.

    def background(self,job,on_success=None):
        # Les commandes TV sont interdites pendant une opération en cours.
        super().background(job,on_success)
        self._update_apply_button()

    def refresh_tv(self):
        self.status.set(self.T('Lecture et inventaire du téléviseur…','Reading TV library and details…'))
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
        self.status.set(self.T(f'TV : {len(photos)} photos · API {version} · base de données actualisée',f'TV: {len(photos)} photos · API {version} · database updated'))
        old.audit('library_read',count=len(photos),api_version=version)

    def choose_folder(self):
        if self.busy:
            messagebox.showinfo(self.T('Opération en cours','Operation in progress'),
                                self.T('Attendez la fin du traitement actuel avant de changer de dossier.',
                                       'Wait until the current operation finishes before changing folders.'))
            return
        # La boîte de dialogue Windows ne permet pas de connaître le nombre de
        # fichiers avant sa fermeture. Dès qu'un dossier est choisi, afficher
        # le début de l'analyse puis envoyer l'avancement depuis le worker.
        folder=filedialog.askdirectory(title=self.T('Dossier des JPEG sources','Source JPEG folder'))
        if not folder:return
        source_dir=Path(folder)
        self.folder=source_dir
        self.status.set(self.T(
            f'Préparation de l’indexation du dossier {source_dir.name}…',
            f'Preparing to index folder {source_dir.name}…'))
        self.update_idletasks()  # visible avant le démarrage du thread

        def task():
            found={};errors=[]
            files=[p for p in sorted(source_dir.iterdir(),key=lambda p:p.name.casefold())
                   if p.is_file() and p.suffix.lower() in ('.jpg','.jpeg','.png')]
            total=len(files)
            self.q.put(('status',self.T(
                f'Indexation des JPEG : 0/{total} · calcul des empreintes…',
                f'Indexing JPEGs: 0/{total} · calculating fingerprints…'),None))
            # Afficher environ 50 étapes au maximum, sans saturer l'interface.
            interval=max(1,(total+49)//50)
            for n,p in enumerate(files,1):
                try:
                    inf=old.photo_info(p);DB.upsert_local(p);found[str(p)]=inf
                except Exception as exc:
                    errors.append((p.name,str(exc)))
                if n==1 or n==total or n%interval==0:
                    self.q.put(('status',self.T(
                        f'Indexation des JPEG : {n}/{total} · {len(errors)} erreur(s) · {p.name}',
                        f'Indexing JPEGs: {n}/{total} · {len(errors)} error(s) · {p.name}'),None))
            self.q.put(('status',self.T(
                f'Indexation {total}/{total} terminée · consolidation de l’historique…',
                f'Indexing {total}/{total} completed · consolidating history…'),None))
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
            self.status.set(self.T(
                f'Dossier {source_dir.name} : {len(found)} fichiers indexés · {len(errors)} erreur(s) · empreintes calculées',
                f'Folder {source_dir.name}: {len(found)} files indexed · {len(errors)} error(s) · fingerprints computed'))
            old.audit('folder_opened',folder=str(source_dir),count=len(found),errors=len(errors))
        self.background(task,done)

    def mark_local_presence(self):
        with DB.lock:
            paths={r['local_path'] for r in DB.db.execute('SELECT local_path FROM tv_photos WHERE present=1 AND local_path IS NOT NULL')}
        for p in self.local_tree.get_children():
            vals=list(self.local_tree.item(p,'values'))
            vals[3]=self.T('Oui','Yes') if str(Path(p).resolve()) in paths else '—'
            self.local_tree.item(p,values=vals)

    def show_selected(self,side):
        self.last_selection_side=side
        if side=='local':
            keys=self.local_tree.selection();self.local_ref=None;self.local_preview.configure(image='',text='')
            if len(keys)!=1:
                self.local_detail.configure(text=(self.T(f'{len(keys)} photos sélectionnées; détails masqués.',f'{len(keys)} photos selected; details hidden.') if keys else self.T('Sélectionnez une photo.','Select a photo.')));return
            info=self.local_items[keys[0]];db=DB.get_local(keys[0]);
            self.local_detail.configure(text=f"{(db or {}).get('source_id') or '—'} · {info['name']}\n{info['width']} × {info['height']} · {info['bytes']/1e6:.2f} Mo\n16:9 : {self.T('Oui','Yes') if info['ratio_ok'] else self.T('Non','No')} · JPEG 1920×1080 : {self.T('Oui','Yes') if info['exact'] and info['format']=='JPEG' else self.T('Non','No')}\nSHA-256 : {(db or {}).get('sha256','?')[:16]}…\npHash : {(db or {}).get('phash','?')}")
            self._image(self.local_preview,keys[0],'local_ref')
            return
        keys=self.tv_tree.selection()
        self._populate_editor(keys)
        self.tv_ref=None;self.tv_preview.configure(image='',text='')
        if len(keys)!=1:
            description=(self.T(f'{len(keys)} photos sélectionnées — paramètres communs dans les menus.',
                                f'{len(keys)} photos selected — common settings in dropdowns.')
                         if keys else self.T('Sélectionnez une photo.','Select a photo.'))
            self.tv_detail.configure(text=description)
            self.tv_preview_status.configure(text=self.T('Miniature : —','Thumbnail: —'))
            return
        cid=keys[0];obj=self.tv_items[cid];rec=DB.get_tv(cid) or {}
        source_bytes=None
        if rec.get('local_path'):
            local=DB.get_local(rec['local_path'])
            source_bytes=(local or {}).get('bytes')
        _,jpeg_detail=tv_jpeg_info(rec,source_bytes,self.lang)
        self.tv_detail.configure(text=(
            f"{cid} · {obj.get('width')}×{obj.get('height')}\n"
            +jpeg_detail+'\n'+self.T('Source : ','Source: ')
            +(Path(rec['local_path']).name if rec.get('local_path') else self.T('correspondance à établir','match not established'))))
        self.tv_preview_status.configure(text=self.T('Miniature : chargement…','Thumbnail: loading…'))
        self.tv_preview.configure(text=self.T('Chargement de la miniature…','Loading thumbnail…'))
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
            widget.configure(image='',text=self.T('Miniature indisponible','Thumbnail unavailable'))
            logging.warning('image %s: %s',path,exc)
            return False

    def poll(self):
        try:
            while True:
                typ,value,callback=self.q.get_nowait()
                if typ=='ok':
                    if callback:callback(value)
                elif typ=='error':
                    self.status.set(self.T('ERREUR : ','ERROR: ')+value)
                    messagebox.showerror(self.T('Opération non confirmée','Operation not confirmed'),value+self.T('\nVérifiez la TV avant de recommencer.','\nCheck your TV before retrying.'))
                elif typ=='status':self.status.set(value)
                elif typ=='batch_progress':
                    stage,index,total,cid=value
                    self.status.set(self.T(
                        f'Analyse JPEG {index}/{total} : {cid}' if stage=='prepare' else f'Remplacement JPEG {index}/{total} : {cid}',
                        f'JPEG analysis {index}/{total}: {cid}' if stage=='prepare' else f'JPEG replacement {index}/{total}: {cid}'))
                elif typ=='tvpreview':
                    cid,p=value
                    if self.tv_tree.selection()==(cid,):
                        ok=self._image(self.tv_preview,p,'tv_ref')
                        self.tv_preview_status.configure(
                            text=self.T('Miniature : affichée','Thumbnail: displayed') if ok else self.T('Miniature : indisponible','Thumbnail: unavailable'))
                elif typ=='tvpreview_error':
                    cid,error=value
                    if self.tv_tree.selection()==(cid,):
                        self.tv_preview.configure(image='',text=self.T('Miniature indisponible','Thumbnail unavailable'))
                        self.tv_preview_status.configure(text=self.T('Miniature : indisponible','Thumbnail: unavailable'))
                        logging.warning('TV miniature %s : %s',cid,error)
                if typ in ('ok','error'):
                    self.busy=False;self.upload_btn.state(['!disabled']);self.delete_btn.state(['!disabled'])
                    self.jpeg_compare_btn.state(['!disabled']);self.jpeg_replace_btn.state(['!disabled'])
                    if self._batch_running:self._batch_end()
                    self._update_apply_button()
        except queue.Empty:pass
        self.after(100,self.poll)

    def change_selected(self):
        ids=list(self.tv_tree.selection())
        if not ids:return
        # Ne jamais déclencher une commande pour la simple sélection d'une photo.
        if not any(self._field_changes().values()):return
        try:plans=self._desired_updates(ids)
        except ValueError as exc:
            return messagebox.showwarning(self.T('Paramètres','Settings'),str(exc))
        if not plans:
            self._update_apply_button()
            return messagebox.showinfo(self.T('Aucun changement','No change'),
                self.T('Les paramètres demandés sont déjà présents sur les photos sélectionnées.',
                       'The selected photos already have the requested settings.'))
        if len(ids)>1:
            with DB.lock:
                successes=DB.db.execute("SELECT COUNT(*) FROM operations WHERE action='change_settings' AND status IN ('confirmé','partiel')").fetchone()[0]
            if not successes:
                return messagebox.showwarning(self.T('Test individuel requis','Single-photo test required'),
                    self.T('Validez une modification individuelle avant une modification groupée.',
                           'Verify one single-photo modification before batch editing.'))
        count=len(plans)
        if not messagebox.askyesno(self.T('Confirmer les modifications','Confirm changes'),
            self.T(f'Appliquer les réglages modifiés à {count} photo(s) sur {len(ids)} sélectionnée(s) ?\n'
                   'Les autres paramètres resteront inchangés.\nLe filtre ne peut pas être relu sur Samsung.',
                   f'Apply changed settings to {count} of {len(ids)} selected photo(s)?\n'
                   'Other settings will stay unchanged.\nArt effects cannot be read back from Samsung.')):return
        def work():
            succeeded=[];cancelled=False
            for n,(cid,matte,filt) in enumerate(plans,1):
                if self._check_stop_between_photos('change_settings',len(succeeded),count-n+1):
                    cancelled=True;break
                self.q.put(('status',self.T(f'Modification {n}/{count} : {cid}',f'Editing {n}/{count}: {cid}'),None))
                try:
                    result=tv_change(cid,matte,filter_id=filt)
                    if filt is not None:DB.filter_set(cid,filt)
                    DB.operation('change_settings',cid=cid,status='confirmé' if filt is None else 'partiel',detail={'requested_matte':matte,'filter_id':filt,'result':result})
                    succeeded.append(cid)
                except Exception as exc:
                    DB.operation('change_settings',cid=cid,status='erreur',detail=str(exc))
                    raise RuntimeError(f'{len(succeeded)} modifications faites; arrêt sur {cid}: {exc}')
            return len(succeeded),cancelled,_tv_read_enriched()
        def done(result):
            count_done,cancelled,library=result
            self.update_tv(library)
            old.audit('batch_change_summary',processed=count_done,requested=count,cancelled=cancelled)
            self.status.set(self.T(
                f'Modification : {count_done}/{count} photos'+(' — arrêt demandé' if cancelled else ''),
                f'Editing: {count_done}/{count} photos'+(' — stopped' if cancelled else '')))
            messagebox.showinfo(self.T('Terminé','Done'),self.T(
                f'{count_done}/{count} photo(s) traitée(s).'+(' Arrêt demandé entre deux photos.' if cancelled else '')+' Passe-partout relu; filtre non vérifiable par photo.',
                f'{count_done}/{count} photo(s) processed.'+(' Stopped between photos.' if cancelled else '')+' Mat read back; effect cannot be independently verified.'))
        if not self._begin_batch('change_settings'):return
        self.background(work,done)
        self._update_apply_button()

    def _source_for_comparison(self):
        # Lorsque les deux listes ont une sélection, comparer la dernière
        # liste utilisée; ne pas imposer la sélection TV à l'utilisateur.
        if getattr(self,'last_selection_side',None)=='local':
            local=self.local_tree.selection()
            if len(local)==1:
                return Path(local[0])
        selected=self.tv_tree.selection()
        if len(selected)==1:
            cid=selected[0]
            rec=DB.get_tv(cid)
            if not rec or not rec.get('local_path'):
                raise ValueError(f'{cid} : aucune correspondance avec un fichier source.\nChoisissez le dossier JPEG original à droite.')
            p=Path(rec['local_path'])
            if not p.is_file():
                raise FileNotFoundError(f'{cid} : source Windows introuvable :\n{p}\nChoisissez le dossier source.')
            return p
        if len(selected)>1:
            raise ValueError('Sélectionnez une seule photo TV pour cette comparaison.')
        paths=self.local_tree.selection()
        if len(paths)!=1:
            raise ValueError('Sélectionnez une photo TV associée ou un JPEG local à droite.')
        return Path(paths[0])

    def compare_selected(self):
        try:source=self._source_for_comparison()
        except Exception as exc:
            return messagebox.showwarning(self.T('Sélection JPEG','JPEG selection'),str(exc))
        self.status.set(self.T(f'Création des 3 variantes JPEG de {source.name}…',f'Creating 3 JPEG variants of {source.name}…'))
        self.background(lambda:jpeg_comparison(source),self.show_jpeg_comparison)
        if self.busy:
            self.jpeg_compare_btn.state(['disabled']);self.jpeg_replace_btn.state(['disabled'])

    def show_jpeg_comparison(self,report):
        win=tk.Toplevel(self)
        win.title(self.T('Comparaison JPEG — ','JPEG comparison — ')+report['source'].name)
        win.geometry('1030x740')
        pane=ttk.Frame(win,padding=12);pane.pack(fill='both',expand=True)
        ttk.Label(pane,text=self.T('Comparaison locale — aucun changement sur le téléviseur','Local comparison — no changes made to the TV'),
                  font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(0,7))
        ttk.Label(pane,text=report['source'].name,foreground='#666').pack(anchor='w')
        summary=ttk.Frame(pane);summary.pack(fill='x',pady=(10,6))
        for n,row in enumerate(report['rows']):
            b=ttk.LabelFrame(summary,text=row['label'],padding=7)
            b.grid(row=0,column=n,sticky='ew',padx=3)
            summary.columnconfigure(n,weight=1)
            delta=(self.T('Original','Original') if row['quality'] is None else
                   (self.T(f"{row['percent']:.1f} % plus léger",f"{row['percent']:.1f}% smaller") if row['percent']>=0
                    else self.T(f"{-row['percent']:.1f} % plus lourd",f"{-row['percent']:.1f}% larger")))
            ttk.Label(b,text=f"{row['bytes']/1e6:.2f} Mo",font=('Segoe UI',11,'bold')).pack()
            ttk.Label(b,text=delta).pack()
        ttk.Label(pane,text=self.T('Extraits centraux de 470 × 225 px, sans redimensionnement (zoom logique 100 %).','Center crops, 470 × 225 px, without resizing (100% logical zoom).'),
                  foreground='#666').pack(anchor='w',pady=(7,6))
        gallery=ttk.Frame(pane);gallery.pack(fill='both',expand=True)
        images=[]
        for n,row in enumerate(report['rows']):
            box=ttk.LabelFrame(gallery,text=row['label'],padding=5)
            box.grid(row=n//2,column=n%2,sticky='nsew',padx=4,pady=4)
            gallery.columnconfigure(n%2,weight=1);gallery.rowconfigure(n//2,weight=1)
            with Image.open(row['path']) as source_im:
                im=ImageOps.exif_transpose(source_im).convert('RGB')
                w,h=im.size
                crop=im.crop(((w-470)//2,(h-225)//2,(w+470)//2,(h+225)//2))
                photo=ImageTk.PhotoImage(crop)
            images.append(photo)
            ttk.Label(box,image=photo).pack(anchor='center',pady=(3,0))
        win._photos=images
        ttk.Label(pane,text=self.T('La taille du JPEG source ne correspond pas forcément à la taille actuellement utilisée sur la TV.\nLa qualité JPEG se juge aussi visuellement ; une réduction peut être négative.','The source JPEG file size may differ from the version currently on the TV.\nJudge JPEG quality visually; optimization can sometimes increase file size.'),
                  foreground='#666').pack(anchor='w',pady=(7,3))
        ttk.Button(pane,text=self.T('Fermer','Close'),command=win.destroy).pack(anchor='e',pady=(5,0))
        self.status.set(self.T(f'Comparaison créée : {report["source"].name} · aucune modification TV',f'Comparison created: {report["source"].name} · TV unchanged'))

    def stop_jpeg_batch(self):
        """Commande d'arrêt pour tous les lots, sans interruption d'une photo."""
        if not self._batch_running:return
        self._stop_batch.set()
        self.stop_batch_btn.state(['disabled'])
        self.status.set(self.T('Arrêt demandé : fin de la photo en cours avant interruption…',
                               'Stop requested: finishing the current photo safely…'))

    def replace_selected_tv(self):
        """Prévisualisation et traitement d'un lot; confirmation avant toute écriture TV."""
        ids=tuple(self.tv_tree.selection())
        if not ids:
            return messagebox.showwarning(self.T('Sélection','Selection'),self.T(
                'Sélectionnez une ou plusieurs photos dans la liste TV (à gauche).',
                'Select one or more photos in the TV list (left).'))
        canonical=self.quality_canonical(self.quality.get())
        if canonical not in LEVELS:
            return messagebox.showwarning(self.T('Qualité JPEG','JPEG quality'),self.T(
                'Choisissez Aucune, JPEG 90, JPEG 85 ou JPEG 80 au centre.',
                'Choose None, JPEG 90, JPEG 85, or JPEG 80 in the center panel.'))
        quality=LEVELS[canonical]
        def prepare():
            return prepare_jpeg_batch(ids,quality,
                progress=lambda i,total,cid:self.q.put(('batch_progress',('prepare',i,total,cid),None)))
        def prepared(plan):
            count=len(plan['ready']);skipped=plan['skipped']
            if not count:
                details='\n'.join(f"{x['cid']} : {x['reason']}" for x in skipped[:9])
                return messagebox.showinfo(self.T('Aucune photo à remplacer','Nothing to replace'),
                    self.T('Toutes les photos sélectionnées ont été écartées.\n',
                           'All selected photos were skipped.\n')+details)
            filt_unknown=sum(not x['filter_known'] for x in plan['ready'])
            skipped_lines='\n'.join(f"{x['cid']}: {x['reason']}" for x in skipped[:6])
            more='…' if len(skipped)>6 else ''
            label=(self.T('Aucune optimisation (JPEG original)','None (original JPEG)')
                   if quality is None else f'JPEG {quality}')
            known=plan['known_count'];unknown=plan['unknown_size_count']
            change=(format_size_variation(plan['net_variation'],self.lang) if known else
                    self.T('non calculable (aucune taille TV connue)','unavailable (no known TV size)'))
            benchmark=format_size_variation(plan['benchmark_delta'],self.lang)
            if self.lang=='en':
                msg=(f'{label} — sequential replacement\n\n'
                     f'Selected: {len(plan["ids"])} | to replace: {count} | skipped: {len(skipped)}\n'
                     f'New JPEG files to transfer: {plan["new_total_bytes"]/1e6:.2f} MB\n'
                     f'Known current TV versions ({known}): {change}\n'
                     f'Compared with source JPEG files (NOT TV sizes): {benchmark}\n'
                     f'Previous JPEG size unknown for {unknown} photo(s); overall change cannot be calculated.\n'
                     f'Among known: {plan["decreases"]} smaller, {plan["increases"]} larger.\n'
                     'These are JPEG byte differences, NOT confirmed Samsung free space.\n'
                     f'Art effect unknown on {filt_unknown} photo(s); it cannot be restored.\n'
                     + (f'\nSkipped:\n{skipped_lines}{more}\n' if skipped else '')+
                     '\nOne by one: upload → verify → restore known settings → verify deletion.\n'
                     'Larger files are allowed, but may be rejected if TV storage is full.\n'
                     'A full database backup will be created. Source JPEG files remain untouched. Continue?')
            else:
                msg=(f'{label} — remplacement séquentiel\n\n'
                     f'Sélection : {len(plan["ids"])} | à remplacer : {count} | écartées : {len(skipped)}\n'
                     f'Nouveaux JPEG à transférer : {plan["new_total_bytes"]/1e6:.2f} Mo\n'
                     f'Versions TV de taille connue ({known}) : {change}\n'
                     f'Comparaison aux JPEG sources (PAS aux tailles TV) : {benchmark}\n'
                     f'Taille de l’ancienne version inconnue pour {unknown} photo(s) : variation totale non calculable.\n'
                     f'Parmi les tailles connues : {plan["decreases"]} réduite(s), {plan["increases"]} augmentée(s).\n'
                     'Ces différences portent sur les octets JPEG, pas sur l’espace libre réel de Samsung.\n'
                     f'Effet artistique inconnu sur {filt_unknown} photo(s) : non récupérable.\n'
                     +(f'\nÉcartées :\n{skipped_lines}{more}\n' if skipped else '')+
                     '\nUne par une : transfert → vérification → restauration → suppression vérifiée.\n'
                     'Un fichier plus volumineux peut être refusé par la TV si elle manque d’espace.\n'
                     'Sauvegarde SQLite avant le lot; JPEG sources intacts. Continuer ?')
            if not messagebox.askyesno(self.T('CONFIRMER le lot JPEG','CONFIRM JPEG batch'),msg,icon='warning'):
                return
            # Le premier callback se termine et libère self.busy dans poll()
            # avant d'autoriser un nouveau background; éviter le conflit d'état.
            self.after_idle(lambda:self._start_jpeg_batch(plan))
        self.background(prepare,prepared)
        if self.busy:
            self.jpeg_compare_btn.state(['disabled']);self.jpeg_replace_btn.state(['disabled'])

    def _start_jpeg_batch(self,plan):
        if self.busy:return
        if not self._begin_batch('replace_jpeg'):return
        def task():
            result=run_jpeg_batch(plan,self._stop_batch,
                progress=lambda i,total,cid:(
                    self.q.put(('batch_progress',('run',i,total,cid),None)),
                    self.q.put(('status',self.T(
                        f'Remplacement {i}/{total} : {cid}',f'Replacing {i}/{total}: {cid}'),None))))
            try:
                library=_tv_read_enriched()
            except Exception as exc:
                library=None
                result['refresh_error']=str(exc)
            return result,library
        def done(data):
            result,library=data
            if library is not None:
                self.update_tv(library)
                processed=result['completed']
                if processed:
                    last=processed[-1]['new_id']
                    if self.tv_tree.exists(last):
                        self.tv_tree.selection_set(last);self.tv_tree.see(last)
                self.mark_local_presence()
            completed=result['completed']
            delta=sum((r['size_new']-r['size_old_tv']) for r in completed if r.get('size_old_tv') is not None)
            known=sum(r.get('size_old_tv') is not None for r in completed)
            change=(format_size_variation(delta,self.lang) if known else
                    self.T('non calculable (taille TV antérieure inconnue)',
                           'unavailable (prior TV size unknown)'))
            # Ces chiffres représentent les tailles d'envoi documentées par Manager,
            # pas le stockage physique réellement libéré par le système Samsung.
            if self.lang=='en':
                summary=(f'Completed: {len(completed)}/{result["ready"]}\n'
                         f'Skipped during preparation: {len(result["skipped"])}\n'
                         f'JPEG byte change on {known} documented prior uploads: {change}\n'
                         f'Previous file size unknown for {len(completed)-known} photo(s).\n'
                         f'Original Windows photos preserved.\n')
                if result['cancelled']:summary+='\nStopped at the end of the previous photo.\n'
                if result['failure']:summary+=f"\nSTOPPED at {result['failure']['cid']}: {result['failure']['error']}\n"
                if result.get('refresh_error'):summary+='\nTV refresh failed: '+result['refresh_error']
            else:
                summary=(f'Remplacements réussis : {len(completed)}/{result["ready"]}\n'
                         f'Écartées avant transfert : {len(result["skipped"])}\n'
                         f'Variation des JPEG sur {known} anciennes versions documentées : {change}\n'
                         f'Taille précédente inconnue pour {len(completed)-known} photo(s).\n'
                         f'Originaux Windows conservés.\n')
                if result['cancelled']:summary+='\nArrêt demandé, effectué entre deux photos.\n'
                if result['failure']:summary+=f"\nARRÊT sur {result['failure']['cid']} : {result['failure']['error']}\n"
                if result.get('refresh_error'):summary+='\nÉchec d’actualisation TV : '+result['refresh_error']
            old.audit('batch_replace_summary',processed=len(completed),planned=result['ready'],
                skipped=len(result['skipped']),cancelled=result['cancelled'],failure=result['failure'])
            if result['failure'] or result.get('refresh_error'):
                self.status.set(self.T('Lot JPEG interrompu — vérifier les détails.','JPEG batch stopped — review details.'))
                messagebox.showwarning(self.T('Lot interrompu','Batch interrupted'),summary)
            else:
                self.status.set(self.T(f'Lot terminé : {len(completed)} remplacée(s), {len(result["skipped"])} écartée(s)',
                    f'Batch done: {len(completed)} replaced, {len(result["skipped"])} skipped'))
                messagebox.showinfo(self.T('Bilan JPEG','JPEG batch summary'),summary)
        self.background(task,done)
        if self.busy:
            self.jpeg_compare_btn.state(['disabled']);self.jpeg_replace_btn.state(['disabled'])

    def upload_selected(self):
        keys=list(self.local_tree.selection())
        if not keys:return messagebox.showinfo(self.T('Sélection','Selection'),self.T('Sélectionnez les fichiers à importer à droite.','Select files to import from the right.'))
        infos=[self.local_items[k] for k in keys]
        try:
            for info in infos:old.verify_upload(info)
        except Exception as exc:return messagebox.showerror(self.T('JPEG incompatible','Incompatible JPEG'),str(exc))
        quality=LEVELS[self.quality_canonical(self.quality.get())];force=self.force.get()
        known=[];unknown=[]
        for info in infos:
            with DB.lock:
                row=DB.db.execute('SELECT content_id FROM tv_photos WHERE local_path=? AND present=1',(str(Path(info['path']).resolve()),)).fetchone()
            if row:known.append(info['name'])
            else:unknown.append(info)
        if known:
            if not unknown:return messagebox.showinfo(self.T('Déjà présentes','Already on TV'),self.T(f'{len(known)} photos reconnues déjà sur la TV. Aucun doublon créé.',f'{len(known)} matching photos are already on the TV. No duplicates created.'))
            messagebox.showinfo(self.T('Doublons évités','Duplicates avoided'),self.T(f'{len(known)} photo(s) reconnue(s) déjà présentes seront exclues du transfert.',f'{len(known)} photo(s) already on the TV will be skipped.'))
        # Ancien contrôle manuel utile, mais le quota Art peut être différent du stockage système.
        if not self.check_storage(upload_infos=unknown):return
        if not messagebox.askyesno(self.T('Confirmer importation','Confirm import'),
            self.T(f'Importer {len(unknown)} JPEG(s) · optimisation : {self.quality.get()} · forcer neutre : {"oui" if force else "non"} ?\nEn cas d’erreur -11, arrêt immédiat.',
                   f'Import {len(unknown)} JPEG(s) · optimization: {self.quality.get()} · force neutral: {"yes" if force else "no"}?\nStop immediately on Samsung -11 error.')):return
        def work():
            completed=[];cancelled=False
            for n,info in enumerate(unknown,1):
                if self._check_stop_between_photos('upload',len(completed),len(unknown)-n+1):
                    cancelled=True;break
                p=Path(info['path']);self.q.put(('status',self.T(f'Transfert {n}/{len(unknown)} : {p.name}',f'Uploading {n}/{len(unknown)}: {p.name}'),None))
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
                    # après confirmation mettre d'abord à jour TV, puis association persistante
                    DB.record_tv_photo(cid,obj)
                    DB.link(cid,p)
                    DB.record_uploaded_version(cid,sendpath,quality)
                    DB.operation('upload',p,cid,'confirmé',{'quality':quality,'file_sha256':sha(sendpath),'matte':matte})
                    completed.append((p.name,cid))
                    if filt is not None:
                        try:
                            tv_change(cid,filter_id=filt)
                            DB.filter_set(cid,filt)
                            DB.operation('filter',p,cid,'envoyé_non_relisible',{'filter':filt})
                        except Exception as ef:
                            DB.operation('filter',p,cid,'non_confirmé',{'filter':filt,'error':str(ef)})
                            raise RuntimeError(f'Photo {cid} déjà importée, mais filtre non appliqué : {ef}')
                except Exception as exc:
                    DB.operation('upload',p,status='erreur',detail=str(exc))
                    old.audit('batch_interrupted',completed=completed,failed=p.name,error=str(exc))
                    raise RuntimeError(f'{len(completed)} photo(s) vérifiée(s) puis arrêt sur {p.name}: {exc}')
                time.sleep(0.5)
            return completed,cancelled,_tv_read_enriched()
        def done(result):
            completed,cancelled,library=result;self.update_tv(library)
            old.audit('batch_upload_summary',completed=len(completed),planned=len(unknown),cancelled=cancelled)
            self.status.set(self.T(
                f'Importation : {len(completed)}/{len(unknown)} photos'+(' — arrêt demandé' if cancelled else ''),
                f'Import: {len(completed)}/{len(unknown)} photos'+(' — stopped' if cancelled else '')))
            messagebox.showinfo(self.T('Importation terminée','Import complete'),self.T(
                f'{len(completed)}/{len(unknown)} photo(s) transférée(s) et vérifiée(s).'+(' Arrêt demandé entre deux photos.' if cancelled else ''),
                f'{len(completed)}/{len(unknown)} photo(s) uploaded and verified.'+(' Stopped between photos.' if cancelled else '')))
        if not self._begin_batch('upload'):return
        self.background(work,done)

    def delete_selected(self):
        ids=list(self.tv_tree.selection())
        if not ids:return messagebox.showinfo(self.T('Sélection','Selection'),self.T('Choisissez des photos TV.','Select TV photos.'))
        if not messagebox.askyesno(self.T('Confirmation suppression','Confirm deletion'),self.T(f'Supprimer définitivement {len(ids)} photo(s) de la TV ?\nLes JPEG de Windows restent intacts.',f'Permanently delete {len(ids)} photo(s) from the TV?\nWindows JPEG files remain untouched.'),icon='warning'):return
        def work():
            done=[];cancelled=False
            for n,cid in enumerate(ids,1):
                if self._check_stop_between_photos('delete',len(done),len(ids)-n+1):
                    cancelled=True;break
                self.q.put(('status',self.T(f'Suppression {n}/{len(ids)} : {cid}',f'Deleting {n}/{len(ids)}: {cid}'),None))
                try:
                    old.tv_delete(cid)
                    DB.operation('delete',cid=cid,status='confirmé');done.append(cid)
                except Exception as exc:
                    DB.operation('delete',cid=cid,status='erreur',detail=str(exc))
                    raise RuntimeError(f'{len(done)} suppressions confirmées, arrêt sur {cid} : {exc}')
            return done,cancelled,_tv_read_enriched()
        def complete(result):
            done,cancelled,library=result;self.update_tv(library)
            old.audit('batch_delete_summary',completed=len(done),planned=len(ids),cancelled=cancelled)
            self.status.set(self.T(
                f'Suppression : {len(done)}/{len(ids)} photos'+(' — arrêt demandé' if cancelled else ''),
                f'Deletion: {len(done)}/{len(ids)} photos'+(' — stopped' if cancelled else '')))
            messagebox.showinfo(self.T('Suppression','Deletion'),self.T(
                f'{len(done)}/{len(ids)} photo(s) supprimée(s), vérification effectuée.'+(' Arrêt demandé entre deux photos.' if cancelled else ''),
                f'{len(done)}/{len(ids)} photo(s) deleted and verified.'+(' Stopped between photos.' if cancelled else '')))
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
        # Accept English (and French) unit expressions, e.g. 2.4 GB.
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
        self.background(DB.export,lambda p:messagebox.showinfo(self.T('Export CSV','Export CSV'),self.T('Exports générés :\n','Exports created:\n')+str(p)))

    def make_backup(self):
        self.background(lambda:DB.backup(self.extras),lambda p:messagebox.showinfo(self.T('Sauvegarde','Backup'),self.T('Base et inventaire sauvegardés :\n','Database and TV inventory backed up:\n')+str(p)+self.T('\nLes JPEG sources restent dans leur dossier original.','\nOriginal JPEGs remain in their source folder.')))


def _read_available_only():
    tv=old.connect()
    try:return tv.available()
    finally:
        try:tv.close()
        except Exception:pass

if __name__=='__main__':
    ManagerV2().mainloop()
