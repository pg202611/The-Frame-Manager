"""The Frame Manager 3.0 — noyau commun.

API Samsung, JPEG, base SQLite et préférences.
Aucune donnée personnelle incluse dans cette distribution.
Les fichiers de données sont créés localement à l’exécution.
"""

# ========== 1. Téléviseur et opérations historiques sécurisées ==========
#!/usr/bin/env python3
"""The Frame Manager v1.2 — Windows 11, Samsung The Frame 2022.
Dépendances : samsungtvws>=3.0.4, Pillow, packaging.
Aucune suppression automatique. Toute écriture est vérifiée par une nouvelle lecture.
"""
import hashlib
import json
import logging
import queue
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

from PIL import Image, ImageTk, ImageOps

IP = ''
MODEL = 'Samsung The Frame'
ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'the_frame_manager_data'
DATA.mkdir(exist_ok=True)
TOKEN = DATA / 'token.txt'
LOG = DATA / 'journal.log'
EVENTS = DATA / 'operations.jsonl'
SESSION = str(uuid.uuid4())
logging.basicConfig(filename=LOG, encoding='utf-8', level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')


def audit(kind, **details):
    rec = {'timestamp': datetime.now().isoformat(timespec='seconds'),
           'session': SESSION, 'event': kind, **details}
    with EVENTS.open('a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + '\n')
    logging.info('%s %s', kind, details)


def photos_unique(items):
    if not isinstance(items, list):
        raise RuntimeError('Réponse bibliothèque inattendue : liste absente')
    found = {}
    for obj in items:
        if not isinstance(obj, dict):
            continue
        cid = str(obj.get('content_id', ''))
        if cid.startswith('MY_') and str(obj.get('content_type', '')).lower() in ('mobile', 'user', 'local', ''):
            found[cid] = obj
    return found


def photo_info(path):
    with Image.open(path) as im:
        w, h = im.size
        fmt = im.format
        im.verify()
    return {'path': str(path), 'name': path.name, 'width': w, 'height': h,
            'ratio': round(w / h, 5) if h else None,
            'ratio_ok': w * 9 == h * 16, 'exact': (w, h) == (1920, 1080),
            'format': fmt, 'bytes': path.stat().st_size}


def verify_upload(info):
    if info['format'] != 'JPEG' or not info['exact'] or info['bytes'] > 15_000_000:
        raise ValueError(f"{info['name']} : JPEG 1920×1080 et taille ≤15 Mo exigés")


def connect():
    from importlib.metadata import version
    from packaging.version import Version
    if Version(version('samsungtvws')) < Version('3.0.4'):
        raise RuntimeError('samsungtvws >= 3.0.4 requis')
    from samsungtvws import SamsungTVArt
    return SamsungTVArt(IP, port=8002, timeout=25, token_file=str(TOKEN),
                        name='TheFrameManagerWindows')


def tv_read():
    tv = connect()
    try:
        version = tv.get_api_version()
        if not version:
            raise RuntimeError('API Art non disponible')
        return photos_unique(tv.available()), str(version)
    finally:
        try: tv.close()
        except Exception: pass


def tv_upload(info):
    verify_upload(info)
    tv = connect()
    try:
        before = photos_unique(tv.available())
        audit('upload_started', filename=info['name'], bytes=info['bytes'])
        cid = tv.upload(info['path'], matte='none', portrait_matte='none', file_type='jpg')
        audit('upload_ack', filename=info['name'], content_id=cid)
        if not isinstance(cid, str) or not cid.startswith('MY_'):
            raise RuntimeError('Identifiant de transfert invalide; vérifiez la TV avant de réessayer')
        for attempt in range(5):
            if attempt: time.sleep(2)
            after = photos_unique(tv.available())
            if cid in after:
                matte = after[cid].get('matte_id')
                if matte != 'none':
                    raise RuntimeError(f'Photo présente ({cid}), mais passe-partout={matte!r}')
                audit('upload_verified', filename=info['name'], content_id=cid, matte=matte,
                      new_id=cid not in before)
                return cid
        raise RuntimeError(f'Transfert annoncé ({cid}) mais non confirmé par lecture; ne pas relancer aveuglément')
    finally:
        try: tv.close()
        except Exception: pass


def tv_delete(cid):
    if not re.fullmatch(r'MY_[A-Za-z0-9_-]+', cid):
        raise ValueError('Suppression refusée : identifiant non personnel')
    tv = connect()
    try:
        before = photos_unique(tv.available())
        if cid not in before:
            raise RuntimeError(f'{cid} absent avant suppression; opération annulée')
        audit('delete_started', content_id=cid)
        # samsungtvws expose delete(content_id) ; une seule suppression par appel.
        tv.delete(cid)
        audit('delete_sent', content_id=cid)
        for attempt in range(5):
            if attempt: time.sleep(2)
            after = photos_unique(tv.available())
            if cid not in after:
                audit('delete_verified', content_id=cid)
                return
        raise RuntimeError(f'Suppression {cid} non confirmée; vérifier avant toute répétition')
    finally:
        try: tv.close()
        except Exception: pass


def _thumbnail_image(data):
    """Décode JPEG/PNG fourni par Samsung et fabrique une vraie miniature JPEG.

    La TV peut renvoyer des données PNG alors que la photo d'origine est JPEG.
    On ne se fie ni au nom renvoyé par Samsung ni à son extension.
    """
    import io
    if not isinstance(data, (bytes, bytearray)) or not 100 < len(data) < 20_000_000:
        raise RuntimeError('Réponse miniature vide ou de taille anormale')
    with Image.open(io.BytesIO(data)) as source:
        source.load()  # Force le décodage : une simple ouverture ne suffit pas.
        img = ImageOps.exif_transpose(source).convert('RGB')
    if not img.width or not img.height:
        raise RuntimeError('Dimensions de miniature invalides')
    original_size = img.size
    img.thumbnail((640, 360), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format='JPEG', quality=90, optimize=True)
    return out.getvalue(), original_size


def _thumbnail_result(result, cid):
    """Interprète la réponse de samsungtvws.get_thumbnail_list().

    L'API nomme ses entrées 'MY_F0003.jpg' ou 'MY_F0003.png', PAS 'MY_F0003'.
    """
    if isinstance(result, dict):
        if not result:
            raise RuntimeError('Samsung a renvoyé une liste de miniatures vide')
        for name, data in result.items():
            if str(name).rsplit('.', 1)[0] == cid:
                return data, str(name)
        if len(result) == 1:
            name, data = next(iter(result.items()))
            logging.warning('Nom de miniature inattendu pour %s : %r', cid, name)
            return data, str(name)
        raise RuntimeError(f'Miniature de {cid} absente de la réponse Samsung')
    if isinstance(result, (bytes, bytearray)):
        return result, cid
    raise RuntimeError(f'Type de réponse miniature inattendu : {type(result).__name__}')


def tv_thumbnail(cid, info=None):
    """Récupère en lecture seule une miniature, avec cache local et journal.

    Pour l'API Art 4.3.4.0 du The Frame 2022, get_thumbnail a été remplacée
    par get_thumbnail_list. Le transfert D2D peut prendre quelques secondes.
    """
    if not re.fullmatch(r'MY_[A-Za-z0-9_-]+', cid):
        raise ValueError('Identifiant de photo personnelle invalide')
    cache = DATA / 'miniatures_v2'
    cache.mkdir(exist_ok=True)
    # Le nom contient la date Samsung : évite un cache périmé si MY_F0001
    # est réattribué à une autre photo après réinitialisation du Smart Hub.
    info = info or {}
    fingerprint = f"{cid}|{info.get('image_date', '')}|{info.get('width', '')}|{info.get('height', '')}"
    suffix = hashlib.sha256(fingerprint.encode('utf-8')).hexdigest()[:12]
    target = cache / (cid + '_' + suffix + '.jpg')
    if target.exists():
        try:
            with Image.open(target) as cached:
                cached.verify()
            return target
        except Exception:
            logging.warning('Cache de miniature illisible : %s ; nouveau téléchargement', target)
    last_error = None
    # Deux tentatives pour des miniatures en cours de génération par Samsung.
    # En cas d'échec, ne modifie rien sur le téléviseur.
    for attempt in range(1, 3):
        tv = None
        try:
            tv = connect()
            version = str(tv.get_api_version())
            if version.startswith('4.'):
                raw, server_name = _thumbnail_result(tv.get_thumbnail_list([cid]), cid)
                method = 'get_thumbnail_list'
            else:
                raw, server_name = _thumbnail_result(tv.get_thumbnail(cid), cid)
                method = 'get_thumbnail'
            image_bytes, dimensions = _thumbnail_image(raw)
            # Écriture atomique pour ne pas créer un cache JPEG tronqué.
            tmp = cache / (target.stem + '_' + uuid.uuid4().hex + '.tmp')
            try:
                tmp.write_bytes(image_bytes)
                tmp.replace(target)
            finally:
                if tmp.exists():
                    tmp.unlink()
            audit('thumbnail_downloaded', content_id=cid, api_version=version,
                  method=method, server_name=server_name, image_size=dimensions,
                  jpeg_bytes=len(image_bytes), attempt=attempt)
            return target
        except Exception as exc:
            last_error = exc
            logging.warning('Miniature %s tentative %d/2 : %s', cid, attempt, exc, exc_info=True)
            if attempt < 2:
                time.sleep(2)
        finally:
            if tv is not None:
                try: tv.close()
                except Exception: pass
    audit('thumbnail_failed', content_id=cid, error=repr(last_error))
    raise RuntimeError(f'Échec de la récupération de la miniature {cid} : {last_error}')


def parse_free_space_mb(value):
    """Lit une mesure MANUELLE de stockage Samsung en Mo ou Go décimaux.

    Ne prétend jamais lire la mémoire de la TV via le réseau.
    """
    text = str(value).replace('\u00a0', ' ').strip().lower()
    match = re.fullmatch(r'(\d+(?:[\.,]\d+)?)\s*(mo|mb|go|gb)', text)
    if not match:
        raise ValueError('Entrez par exemple 2,4 Go ou 850 Mo (unité obligatoire).')
    qty = float(match.group(1).replace(',', '.'))
    if not (0 < qty < 1_000_000):
        raise ValueError('Valeur d’espace libre invalide.')
    return qty * (1000 if match.group(2) in ('go', 'gb') else 1)


def estimate_capacity(infos, free_mb):
    """Vérification conservatrice non garantie: fichiers + 50 % + réserve 300 Mo.

    La taille interne finale, les caches et le quota Art sont inconnus.
    """
    if not infos:
        raise ValueError('Aucune photo à analyser.')
    total_bytes = sum(int(item['bytes']) for item in infos)
    if total_bytes <= 0:
        raise ValueError('La taille totale des fichiers est nulle.')
    total_mb = total_bytes / 1_000_000
    recommended_mb = 1.5 * total_mb + 300
    return {'count': len(infos), 'sum_bytes': total_bytes, 'sum_mb': total_mb,
            'free_mb': float(free_mb), 'recommended_mb': recommended_mb,
            'surplus_mb': float(free_mb) - recommended_mb,
            'probably_enough': float(free_mb) >= recommended_mb}


class Manager(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title('The Frame Manager v1.2 — Samsung 32 po')
        self.geometry('1240x780')
        self.minsize(920, 590)
        self.tv_items = {}
        self.local_items = {}
        self.tv_ids = []
        self.local_ids = []
        self.q = queue.Queue()
        self.busy = False
        self.photo_ref = None
        self.folder = None
        self.status = tk.StringVar(value='Prêt — actualisez les photos du téléviseur.')
        self.build()
        self.after(100, self.poll)
        audit('session_start', ip=IP, model=MODEL)
        self.protocol('WM_DELETE_WINDOW', self.quit_app)
        if IP:
            self.refresh_tv()
        else:
            self.status.set('Configurez l’adresse IP du téléviseur dans Paramètres pour commencer. / Set TV IP in Settings.')

    def build(self):
        style = ttk.Style(self)
        if 'vista' in style.theme_names(): style.theme_use('vista')
        top = ttk.Frame(self, padding=12)
        top.pack(fill='x')
        ttk.Label(top, text='THE FRAME MANAGER 1.2', font=('Segoe UI', 17, 'bold')).pack(side='left')
        ttk.Label(top, text=f'   {MODEL}  •  {IP}', foreground='#666').pack(side='left')
        ttk.Button(top, text='Actualiser la TV', command=self.refresh_tv).pack(side='right')
        pane = ttk.Frame(self, padding=(12, 4, 12, 4))
        pane.pack(fill='both', expand=True)
        pane.columnconfigure(0, weight=1)
        pane.columnconfigure(2, weight=1)
        pane.rowconfigure(0, weight=1)
        left = ttk.LabelFrame(pane, text='Photos sur la TV', padding=8)
        left.grid(row=0, column=0, sticky='nsew')
        right = ttk.LabelFrame(pane, text='Photos à ajouter — ordinateur', padding=8)
        right.grid(row=0, column=2, sticky='nsew')
        middle = ttk.Frame(pane, padding=(8, 20))
        middle.grid(row=0, column=1, sticky='ns')
        self.tv_tree = self.make_tree(left, ('Identifiant', 'Résolution', 'Passe-partout'))
        self.local_tree = self.make_tree(right, ('Fichier', 'Résolution', '16:9'))
        self.tv_tree.bind('<<TreeviewSelect>>', lambda _: self.show_selected('tv'))
        self.local_tree.bind('<<TreeviewSelect>>', lambda _: self.show_selected('local'))
        ttk.Button(right, text='Choisir un dossier…', command=self.choose_folder).pack(fill='x', pady=(8, 0))
        ttk.Label(middle, text='Opérations', font=('Segoe UI', 10, 'bold')).pack(pady=(45, 15))
        self.upload_btn = ttk.Button(middle, text='← Ajouter', command=self.upload_selected)
        self.upload_btn.pack(fill='x', pady=5)
        self.delete_btn = ttk.Button(middle, text='✕ Supprimer', command=self.delete_selected)
        self.delete_btn.pack(fill='x', pady=5)
        ttk.Button(middle, text='Espace disponible…', command=self.check_storage).pack(fill='x', pady=(16, 4))
        ttk.Label(middle, text='Sélection multiple :\nCtrl ou Maj + clic', justify='center').pack(pady=20)
        bottom = ttk.LabelFrame(self, text='Détails de la sélection', padding=10)
        bottom.pack(fill='x', padx=12, pady=(4, 8))
        details = ttk.Frame(bottom)
        details.pack(fill='x')
        self.preview = ttk.Label(details, text='Aucune miniature', anchor='center', width=34)
        self.preview.pack(side='left', padx=(0, 16))
        self.detail = ttk.Label(details, text='Sélectionnez une photo.', justify='left', anchor='nw')
        self.detail.pack(side='left', fill='both', expand=True)
        ttk.Label(self, textvariable=self.status, relief='sunken', anchor='w', padding=6).pack(fill='x')

    def make_tree(self, parent, columns):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='extended')
        for i, col in enumerate(columns):
            tree.heading(col, text=col)
            tree.column(col, width=170 if i == 0 else 110, minwidth=80)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side='left', fill='both', expand=True)
        scroll.pack(side='right', fill='y')
        return tree

    def background(self, job, on_success=None):
        if self.busy:
            messagebox.showinfo('Opération en cours', 'Attendez la fin de l’opération actuelle.')
            return
        self.busy = True
        self.upload_btn.state(['disabled'])
        self.delete_btn.state(['disabled'])
        def worker():
            try:
                result = job()
                self.q.put(('ok', result, on_success))
            except Exception as exc:
                logging.exception('Échec opération')
                audit('operation_error', error=repr(exc))
                self.q.put(('error', str(exc), None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                typ, value, callback = self.q.get_nowait()
                if typ == 'ok':
                    if callback: callback(value)
                elif typ == 'error':
                    self.status.set('ERREUR : ' + value)
                    messagebox.showerror('Opération non confirmée', value + '\n\nVérifiez la TV avant toute nouvelle tentative.')
                elif typ == 'preview':
                    self.show_preview(value)
                elif typ == 'status':
                    self.status.set(value)
                if typ in ('ok', 'error'):
                    self.busy = False
                    self.upload_btn.state(['!disabled'])
                    self.delete_btn.state(['!disabled'])
        except queue.Empty:
            pass
        self.after(100, self.poll)

    def refresh_tv(self):
        self.status.set('Lecture de la bibliothèque Samsung…')
        self.background(tv_read, self.update_tv)

    def update_tv(self, result):
        photos, version = result
        self.tv_items = photos
        self.tv_tree.delete(*self.tv_tree.get_children())
        for cid, info in sorted(photos.items()):
            self.tv_tree.insert('', 'end', iid=cid, values=(cid,
                f"{info.get('width', '?')}×{info.get('height', '?')}", info.get('matte_id', '?')))
        self.status.set(f'TV : {len(photos)} photo(s) personnelles uniques · API Art {version}')
        audit('library_read', count=len(photos), api_version=version)

    def choose_folder(self):
        folder = filedialog.askdirectory(title='Choisir le dossier de photos JPEG')
        if not folder: return
        self.folder = Path(folder)
        self.local_tree.delete(*self.local_tree.get_children())
        self.local_items = {}
        errors = 0
        for path in sorted(self.folder.iterdir(), key=lambda p: p.name.casefold()):
            if path.suffix.lower() not in ('.jpg', '.jpeg', '.png') or not path.is_file(): continue
            try:
                info = photo_info(path)
                self.local_items[str(path)] = info
                self.local_tree.insert('', 'end', iid=str(path), values=(path.name,
                    f"{info['width']}×{info['height']}", 'Oui' if info['ratio_ok'] else 'Non'))
            except Exception:
                errors += 1
        self.status.set(f'Dossier : {len(self.local_items)} photo(s), {errors} fichier(s) illisible(s).')
        audit('folder_opened', folder=str(self.folder), count=len(self.local_items), errors=errors)

    def show_selected(self, side):
        tree = self.tv_tree if side == 'tv' else self.local_tree
        selected = tree.selection()
        self.preview.configure(image='', text='Miniature en cours…' if len(selected) == 1 and side == 'tv' else '')
        self.photo_ref = None
        if len(selected) != 1:
            self.detail.configure(text=f'{len(selected)} élément(s) sélectionné(s).\nAucun détail individuel affiché.' if selected else 'Sélectionnez une photo.')
            return
        key = selected[0]
        if side == 'local':
            info = self.local_items[key]
            self.detail.configure(text=f"Fichier : {info['name']}\nDimensions : {info['width']} × {info['height']}\n"
                f"Format : {info['format']}\nRatio exact 16:9 : {'OUI' if info['ratio_ok'] else 'NON'}\n"
                f"Prêt pour la TV (JPEG 1920×1080) : {'OUI' if info['format']=='JPEG' and info['exact'] and info['bytes']<=15_000_000 else 'NON'}\n"
                f"Taille : {info['bytes']/1024/1024:.2f} Mo\nChemin : {info['path']}")
            self.show_preview(Path(key))
        else:
            info = self.tv_items[key]
            self.detail.configure(text=f"Identifiant Samsung : {key}\nType : {info.get('content_type','?')}\n"
                f"Dimensions : {info.get('width','?')} × {info.get('height','?')}\n"
                f"Passe-partout : {info.get('matte_id','?')}\n"
                f"Date Samsung : {info.get('image_date','non fournie')}\n"
                'Miniature : récupération en cours…')
            def work():
                try: p = tv_thumbnail(key, info)
                except Exception as exc:
                    self.q.put(('preview', ('error', key, str(exc)), None))
                    return
                self.q.put(('preview', ('path', key, p), None))
            threading.Thread(target=work, daemon=True).start()

    def show_preview(self, value):
        if isinstance(value, tuple):
            kind, cid, payload = value
            if self.tv_tree.selection() != (cid,): return
            if kind == 'error':
                self.preview.configure(text='Miniature indisponible')
                self.detail.configure(text=self.detail.cget('text').replace('récupération en cours…', payload))
                return
            path = payload
        else: path = value
        try:
            with Image.open(path) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((300, 175), Image.Resampling.LANCZOS)
                self.photo_ref = ImageTk.PhotoImage(im.copy())
            self.preview.configure(image=self.photo_ref, text='')
            if isinstance(value, tuple):
                self.detail.configure(text=self.detail.cget('text').replace('récupération en cours…', 'disponible'))
        except Exception as exc:
            self.preview.configure(text='Miniature illisible')
            logging.warning('Miniature illisible : %s', exc)


    def check_storage(self, upload_infos=None):
        """Compare taille JPEG du lot et libre TV fourni par l'utilisateur.

        En mode upload, aucune importation n'est autorisée si la comparaison est
        défavorable ou si l'utilisateur annule la saisie.
        """
        if upload_infos is None:
            keys = list(self.local_tree.selection())
            candidates = [self.local_items[k] for k in keys] if keys else list(self.local_items.values())
            infos = []
            skipped = 0
            for inf in candidates:
                try:
                    verify_upload(inf)
                    infos.append(inf)
                except ValueError:
                    skipped += 1
            if not infos:
                messagebox.showinfo('Espace disponible',
                    'Choisissez un dossier avec des JPEG 1920 × 1080 compatibles.')
                return False
        else:
            infos = list(upload_infos)
            skipped = 0
        total_mb = sum(int(info['bytes']) for info in infos) / 1_000_000
        ask = (
            f'{len(infos)} photo(s) analysée(s) : {total_mb:.1f} Mo de fichiers JPEG.\n'
            + (f'{skipped} fichier(s) non compatibles exclus du calcul.\n' if skipped else '')
            + '\nSur le téléviseur : Paramètres > Assistance > Entretien de l’appareil'
            + ' > Gérer le stockage.\n'
            + 'Relevez l’ESPACE LIBRE et entrez sa valeur avec unité.\n'
            + 'Exemples : 2,4 Go ou 850 Mo.\n'
            + '\nIl s’agit d’une mesure manuelle : le logiciel ne lit pas cette valeur via l’API Art.'
        )
        entered = simpledialog.askstring('Vérification de l’espace — The Frame', ask, parent=self)
        if entered is None:
            audit('storage_preflight_cancelled', count=len(infos), sum_mb=round(total_mb, 2))
            return False
        try:
            free_mb = parse_free_space_mb(entered)
            result = estimate_capacity(infos, free_mb)
        except ValueError as exc:
            messagebox.showerror('Valeur invalide', str(exc))
            return False
        audit('storage_preflight', count=result['count'], sum_mb=round(result['sum_mb'], 2),
              free_mb=result['free_mb'], recommended_mb=round(result['recommended_mb'], 2),
              probably_enough=result['probably_enough'], source='user_manual_tv_display',
              excluded_incompatible=skipped)
        report = (
            f"Photos compatibles : {result['count']}\n"
            f"Taille totale des JPEG : {result['sum_mb']:.1f} Mo\n"
            f"Espace libre déclaré de la TV : {result['free_mb']:.1f} Mo\n"
            f"Seuil de prudence : {result['recommended_mb']:.1f} Mo\n\n"
            'Seuil non officiel = taille JPEG × 1,5 + 300 Mo de réserve.\n'
            'Samsung peut conserver des fichiers et des caches supplémentaires.\n'
            'L’espace global TV peut différer du quota disponible pour les photos Art.\n'
        )
        if result['probably_enough']:
            messagebox.showinfo('Espace : comparaison favorable',
                report + '\nComparaison favorable, sans garantie de capacité Art. '
                'Chaque importation restera vérifiée individuellement.')
            return True
        messagebox.showwarning('Espace possiblement insuffisant',
            report + '\nLa marge est insuffisante selon notre règle prudente. '
            'Réduisez le lot ou libérez de la mémoire avant de transférer.')
        return False

    def upload_selected(self):
        keys = list(self.local_tree.selection())
        if not keys:
            messagebox.showinfo('Sélection', 'Sélectionnez une ou plusieurs photos dans la liste de droite.')
            return
        infos = [self.local_items[k] for k in keys]
        try:
            for info in infos: verify_upload(info)
        except ValueError as exc:
            messagebox.showerror('Fichier non compatible', str(exc))
            return
        # Contrôle d'espace avant TOUTE écriture. Valeur Samsung saisie par l'utilisateur.
        if not self.check_storage(upload_infos=infos):
            return
        if not messagebox.askyesno('Confirmer le transfert',
                f'Ajouter {len(infos)} photo(s) à la TV, sans passe-partout ?\n'
                'Chaque transfert sera vérifié. Aucun fichier existant ne sera supprimé.'):
            return
        def work():
            completed = []
            for n, info in enumerate(infos, 1):
                self.q.put(('status', f'Importation {n}/{len(infos)} : {info["name"]}', None))
                try:
                    cid = tv_upload(info)
                    completed.append((info['name'], cid))
                except Exception as exc:
                    audit('batch_interrupted', completed=completed, failed=info['name'], error=str(exc))
                    raise RuntimeError(f'{len(completed)} import(s) confirmé(s), arrêt sur {info["name"]} : {exc}')
                time.sleep(1)
            audit('batch_upload_finished', completed=completed)
            return completed, tv_read()
        def done(result):
            completed, library = result
            self.update_tv(library)
            messagebox.showinfo('Importation terminée', f'{len(completed)} photo(s) importée(s) et vérifiée(s), passe-partout none.')
        self.background(work, done)

    def delete_selected(self):
        ids = list(self.tv_tree.selection())
        if not ids:
            messagebox.showinfo('Sélection', 'Sélectionnez une ou plusieurs photos dans la liste de gauche.')
            return
        if not all(cid.startswith('MY_') for cid in ids):
            messagebox.showerror('Protection', 'La suppression est réservée aux photos personnelles.')
            return
        if not messagebox.askyesno('CONFIRMER LA SUPPRESSION',
                f'Supprimer définitivement {len(ids)} photo(s) du téléviseur ?\n\n'
                + '\n'.join(ids[:10]) + ('\n…' if len(ids)>10 else '')
                + '\n\nCette action ne peut pas être annulée.', icon='warning'):
            return
        def work():
            completed = []
            for n, cid in enumerate(ids, 1):
                self.q.put(('status', f'Suppression {n}/{len(ids)} : {cid}', None))
                try:
                    tv_delete(cid)
                    completed.append(cid)
                except Exception as exc:
                    audit('delete_batch_interrupted', completed=completed, failed=cid, error=str(exc))
                    raise RuntimeError(f'{len(completed)} suppression(s) confirmée(s), arrêt sur {cid} : {exc}')
            audit('batch_delete_finished', completed=completed)
            return completed, tv_read()
        def done(result):
            completed, library = result
            self.update_tv(library)
            messagebox.showinfo('Suppression terminée', f'{len(completed)} photo(s) supprimée(s) et absence vérifiée.')
        self.background(work, done)

    def quit_app(self):
        if self.busy:
            if not messagebox.askyesno('Opération en cours', 'Une opération est en cours. Quitter pourrait interrompre sa vérification. Quitter ?'):
                return
        audit('session_end')
        self.destroy()



# ========== 2. Persistance SQLite et traitement JPEG ==========
"""Noyau persistant de The Frame Manager v2, sans effets de bord sur la TV."""
import csv, hashlib, json, os, sqlite3, threading, uuid, time
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageOps

LEVELS = {'Aucune': None, 'JPEG 90':90, 'JPEG 85':85, 'JPEG 80':80}

def now(): return datetime.now().isoformat(timespec='seconds')
def sha(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for c in iter(lambda:f.read(1024*1024),b''): h.update(c)
    return h.hexdigest()
def phash(path):
    """pHash 64 bits DCT sans numpy; bit-significatif stable, non preuve unique."""
    import math
    with Image.open(path) as source:
        im=ImageOps.exif_transpose(source).convert('L').resize((32,32),Image.Resampling.LANCZOS)
        pixels=list(im.getdata())
    # DCT séparable: 1024x8x2 additions plutôt que 64 x 1024 boucles trig
    ct=[[math.cos((2*x+1)*u*math.pi/64) for x in range(32)] for u in range(8)]
    rows=[[sum(pixels[y*32+x]*ct[u][x] for x in range(32)) for u in range(8)] for y in range(32)]
    coeff=[sum(rows[y][u]*ct[v][y] for y in range(32)) for v in range(8) for u in range(8)]
    a=sorted(coeff[1:]); median=(a[31]+a[32])/2
    bits=0
    for n,val in enumerate(coeff):
        if val>median: bits|=1<<(63-n)
    return f'{bits:016x}'

def ham(a,b):return (int(a,16)^int(b,16)).bit_count()

def optimize(source,quality,root):
    source=Path(source); digest=sha(source)
    dest=Path(root)/'Optimisations'/f'JPEG_{quality}'/f'{digest[:20]}_q{quality}.jpg'
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        try:
            with Image.open(dest) as im:
                im.verify()
            return dest
        except Exception:dest.unlink()
    tmp=dest.with_suffix('.'+uuid.uuid4().hex+'.tmp')
    try:
        with Image.open(source) as im:
            exif=im.getexif().tobytes() if im.getexif() else None
            icc=im.info.get('icc_profile')
            # source Cropper déjà 1920x1080, pas de recadrage ni redimensionnement
            if im.format!='JPEG' or im.size!=(1920,1080): raise ValueError('JPEG 1920x1080 requis')
            opts={'format':'JPEG','quality':quality,'subsampling':0,'optimize':True}
            if exif:opts['exif']=exif
            if icc:opts['icc_profile']=icc
            im.save(tmp,**opts)
        os.replace(tmp,dest)
    finally:
        if tmp.exists():tmp.unlink()
    return dest

class Registry:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/'manager_v2.sqlite';self.lock=threading.RLock()
        self.db=sqlite3.connect(self.path,check_same_thread=False,timeout=30)
        self.db.row_factory=sqlite3.Row
        with self.lock:
            self.db.executescript('''PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS local_photos(path TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL, phash TEXT NOT NULL, bytes INTEGER NOT NULL, width INTEGER, height INTEGER, seen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS versions(path TEXT PRIMARY KEY, source_path TEXT NOT NULL, quality INTEGER, sha256 TEXT NOT NULL, phash TEXT, bytes INTEGER, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tv_photos(content_id TEXT PRIMARY KEY, raw_json TEXT NOT NULL, matte_id TEXT, portrait_matte_id TEXT, filter_id TEXT, filter_source TEXT, local_path TEXT, match_method TEXT, match_distance INTEGER, present INTEGER NOT NULL, seen_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, action TEXT, local_path TEXT, content_id TEXT, status TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT, kind TEXT, payload TEXT);
            CREATE INDEX IF NOT EXISTS idx_local_sha ON local_photos(sha256);
            CREATE INDEX IF NOT EXISTS idx_tv_local ON tv_photos(local_path);
            ''')
            # Migration non destructive des bases SQLite v2.0 à v2.0.2.
            present={row['name'] for row in self.db.execute('PRAGMA table_info(tv_photos)')}
            for field,decl in (
                ('uploaded_sha256','TEXT'),('uploaded_bytes','INTEGER'),
                ('uploaded_quality','INTEGER'),('uploaded_version_path','TEXT')):
                if field not in present:
                    self.db.execute(f'ALTER TABLE tv_photos ADD COLUMN {field} {decl}')
            # Identifiant SOURCE : stable d'une session à l'autre et indépendant
            # des identifiants MY_Fxxxx attribués par Samsung lors des remplacements.
            loc_cols={r['name'] for r in self.db.execute('PRAGMA table_info(local_photos)')}
            if 'source_id' not in loc_cols:
                self.db.execute('ALTER TABLE local_photos ADD COLUMN source_id TEXT')
            self._assign_missing_source_ids_locked()
            self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_local_source_id ON local_photos(source_id)')
            self.db.commit()

    def _assign_missing_source_ids_locked(self):
        """À appeler avec self.lock détenu, avant le commit de la transaction.

        Les ID existants ne changent jamais; les nouveaux sont attribués par
        ordre de nom de fichier, de façon reproductible lors de la migration.
        """
        rows=self.db.execute("SELECT source_id FROM local_photos WHERE source_id LIKE 'SRC-%'").fetchall()
        sequence=max((int(row[0][4:]) for row in rows if row[0][4:].isdigit()),default=0)
        missing=self.db.execute("""SELECT path FROM local_photos
            WHERE source_id IS NULL OR source_id='' ORDER BY filename COLLATE NOCASE, path""").fetchall()
        for row in missing:
            sequence+=1
            self.db.execute('UPDATE local_photos SET source_id=? WHERE path=?',
                            (f'SRC-{sequence:05d}',row['path']))

    def source_ids(self):
        with self.lock:
            return {row['path']:row['source_id'] for row in
                    self.db.execute('SELECT path,source_id FROM local_photos')}

    def operation(self,action,path=None,cid=None,status='info',detail=None):
        with self.lock:
            self.db.execute('INSERT INTO operations(time,action,local_path,content_id,status,detail) VALUES(?,?,?,?,?,?)',(now(),action,str(path) if path else None,cid,status,json.dumps(detail,ensure_ascii=False,default=str) if detail is not None else None));self.db.commit()
    def upsert_local(self,path,info=None):
        path=Path(path).resolve()
        with Image.open(path) as im:
            w,h=im.size
        rec=(str(path),path.name,sha(path),phash(path),path.stat().st_size,w,h,now())
        with self.lock:
            self.db.execute('''INSERT INTO local_photos(path,filename,sha256,phash,bytes,width,height,seen_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET
                filename=excluded.filename,sha256=excluded.sha256,phash=excluded.phash,
                bytes=excluded.bytes,width=excluded.width,height=excluded.height,seen_at=excluded.seen_at''',rec)
            self._assign_missing_source_ids_locked()
            self.db.commit()
        return {'path':str(path),'sha256':rec[2],'phash':rec[3]}
    def add_version(self,path,source,quality):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO versions VALUES(?,?,?,?,?,?,?)',(str(path),str(source),quality,sha(path),phash(path),path.stat().st_size,now()));self.db.commit()
    def get_tv(self,cid):
        with self.lock:
            row=self.db.execute('SELECT * FROM tv_photos WHERE content_id=?',(cid,)).fetchone();return dict(row) if row else None
    def get_local(self,path):
        with self.lock:
            row=self.db.execute('SELECT * FROM local_photos WHERE path=?',(str(Path(path).resolve()),)).fetchone();return dict(row) if row else None
    def match_thumb(self,path):
        p=phash(path)
        with self.lock:
            rows=self.db.execute('SELECT path, phash FROM local_photos').fetchall()
        if not rows:return None,'aucun fichier local',None
        distances=sorted((ham(p,r['phash']),r['path']) for r in rows)
        if distances[0][0]<=5 and (len(distances)==1 or distances[1][0]-distances[0][0]>=5):
            return distances[0][1],'pHash probable (non confirmé)',distances[0][0]
        return None,'ambigu ou éloigné',distances[0][0]
    def tv_inventory(self,photos):
        stamp=now()
        with self.lock:
            self.db.execute('UPDATE tv_photos SET present=0')
            for cid,obj in photos.items():
                prev=self.db.execute('SELECT * FROM tv_photos WHERE content_id=?',(cid,)).fetchone()
                if prev:
                    filt,fs,loc,method,dist=prev['filter_id'],prev['filter_source'],prev['local_path'],prev['match_method'],prev['match_distance']
                else:filt=fs=loc=method=dist=None
                self.db.execute('''INSERT INTO tv_photos(content_id,raw_json,matte_id,portrait_matte_id,filter_id,filter_source,local_path,match_method,match_distance,present,seen_at)
                    VALUES(?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(content_id) DO UPDATE SET raw_json=excluded.raw_json,matte_id=excluded.matte_id,portrait_matte_id=excluded.portrait_matte_id,present=1,seen_at=excluded.seen_at''',
                    (cid,json.dumps(obj,ensure_ascii=False),obj.get('matte_id'),obj.get('portrait_matte_id'),filt,fs,loc,method,dist,stamp))
            self.db.execute('INSERT INTO snapshots(time,kind,payload) VALUES(?,?,?)',(stamp,'tv_inventory',json.dumps(photos,ensure_ascii=False)));self.db.commit()
    def record_tv_photo(self,cid,obj):
        """Ajoute/actualise UNE photo confirmée sans marquer les autres absentes."""
        with self.lock:
            prev=self.db.execute('SELECT * FROM tv_photos WHERE content_id=?',(cid,)).fetchone()
            fs=prev['filter_source'] if prev else None
            filt=prev['filter_id'] if prev else None
            loc=prev['local_path'] if prev else None
            method=prev['match_method'] if prev else None
            dist=prev['match_distance'] if prev else None
            self.db.execute('''INSERT INTO tv_photos(content_id,raw_json,matte_id,portrait_matte_id,filter_id,filter_source,local_path,match_method,match_distance,present,seen_at)
                VALUES(?,?,?,?,?,?,?,?,?,1,?) ON CONFLICT(content_id) DO UPDATE SET
                raw_json=excluded.raw_json,matte_id=excluded.matte_id,
                portrait_matte_id=excluded.portrait_matte_id,present=1,seen_at=excluded.seen_at''',
                (cid,json.dumps(obj,ensure_ascii=False),obj.get('matte_id'),obj.get('portrait_matte_id'),filt,fs,loc,method,dist,now()))
            self.db.commit()
    def record_uploaded_version(self,cid,uploaded_path,quality):
        """Indique la version envoyée, sans déduire la version des photos anciennes."""
        uploaded_path=Path(uploaded_path)
        with self.lock:
            self.db.execute('''UPDATE tv_photos
                SET uploaded_sha256=?,uploaded_bytes=?,uploaded_quality=?,uploaded_version_path=?
                WHERE content_id=?''',
                (sha(uploaded_path),uploaded_path.stat().st_size,quality,
                 str(uploaded_path),cid))
            self.db.commit()

    def mark_replaced(self,old_cid,new_cid):
        """Marque l'ancien ID absent UNIQUEMENT après suppression vérifiée de la TV."""
        with self.lock:
            self.db.execute('UPDATE tv_photos SET present=0 WHERE content_id=?',(old_cid,))
            self.db.commit()

    def link(self,cid,source,method='transfert confirmé',distance=None):
        with self.lock:
            self.db.execute('UPDATE tv_photos SET local_path=?,match_method=?,match_distance=? WHERE content_id=?',(str(Path(source).resolve()),method,distance,cid));self.db.commit()
    def filter_set(self,cid,filter_id):
        with self.lock:
            self.db.execute('UPDATE tv_photos SET filter_id=?,filter_source=? WHERE content_id=?',(filter_id,'écrit par Manager — lecture de confirmation indisponible',cid));self.db.commit()
    def import_old_logs(self,events):
        """Importation de traces vérifiées antérieures, sans inventer de chemin ni de SHA."""
        log=Path(events)
        if not log.exists():return 0
        pairs=[];startfiles={}
        for ln in log.open(encoding='utf-8-sig',errors='replace'):
            try:rec=json.loads(ln)
            except Exception:continue
            if rec.get('event')=='upload_verified' and rec.get('content_id') and rec.get('filename'):
                pairs.append((rec['content_id'],rec['filename']))
        with self.lock:
            for cid,filename in pairs:
                row=self.db.execute('SELECT local_path FROM tv_photos WHERE content_id=?',(cid,)).fetchone()
                if not row or row['local_path']:continue
                matches=self.db.execute('SELECT path FROM local_photos WHERE filename=?',(filename,)).fetchall()
                if len(matches)==1:
                    self.db.execute('UPDATE tv_photos SET local_path=?,match_method=? WHERE content_id=?',(matches[0]['path'],'journal upload_verified',cid))
            self.db.commit()
        return len(pairs)
    def export(self):
        folder=self.root/'Exports';folder.mkdir(exist_ok=True)
        with self.lock:
            for table in ('local_photos','versions','tv_photos','operations'):
                cur=self.db.execute('SELECT * FROM '+table)
                with (folder/(table+'.csv')).open('w',newline='',encoding='utf-8-sig') as f:
                    writer=csv.writer(f);writer.writerow([d[0] for d in cur.description]);writer.writerows(cur.fetchall())
        return folder
    def backup(self,extra=None):
        folder=self.root/'Sauvegardes'/datetime.now().strftime('%Y-%m-%d_%H%M%S')
        folder.mkdir(parents=True,exist_ok=True)
        with self.lock:
            dst=sqlite3.connect(folder/'manager_v2.sqlite');self.db.backup(dst);dst.close()
            content=[dict(r) for r in self.db.execute('SELECT * FROM tv_photos WHERE present=1')]
        (folder/'photos_tv.json').write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding='utf-8')
        (folder/'parametres_tv.json').write_text(json.dumps(extra or {},ensure_ascii=False,indent=2),encoding='utf-8')
        return folder


# ========== 3. Configuration et préférences ==========
"""Préférences locales non sensibles pour The Frame Manager.
Stockées dans the_frame_manager_data, indépendamment des scripts mis à jour.
"""
from pathlib import Path
import json, ipaddress, os, tempfile

DEFAULTS = {
    'ip': '',
    'language': 'fr',
    'jpeg_quality': 'Aucune',
    'force_neutral': False,
    'matte_style': '(ne pas modifier)',
    'matte_color': 'polar',
    'art_filter': '(ne pas modifier)',
    'column_widths': {'tv': [110, 92, 97, 130, 58], 'local': [96, 275, 98, 55]},
}
QUALITIES = ('Aucune', 'JPEG 90', 'JPEG 85', 'JPEG 80')
MATTES = ('(ne pas modifier)', 'none', 'modernthin','modern','modernwide','flexible','shadowbox','panoramic','triptych','mix','squares')
COLORS = ('black','neutral','antique','warm','polar','sand','seafoam','sage','burgandy','navy','apricot','byzantine','lavender','redorange','skyblue','turquoise')
FILTERS = ('(ne pas modifier)','None','Aqua','ArtDeco','Ink','Wash','Pastel','Feuve')


def valid_ip(value):
    value = str(value).strip()
    try:
        addr = ipaddress.ip_address(value)
    except ValueError as e:
        raise ValueError('Adresse IPv4 invalide / Invalid IPv4 address') from e
    if addr.version != 4 or addr.is_multicast or addr.is_unspecified or addr.is_loopback or addr.is_reserved:
        raise ValueError('Adresse IPv4 locale requise / Local IPv4 address required')
    return str(addr)


def normalize(raw):
    raw = raw if isinstance(raw,dict) else {}
    out = dict(DEFAULTS)
    out['column_widths']={side:list(widths) for side,widths in DEFAULTS['column_widths'].items()}
    try:out['ip'] = valid_ip(raw.get('ip',DEFAULTS['ip']))
    except ValueError:pass
    if raw.get('language') in ('fr','en'):out['language'] = raw['language']
    for key,choices in [('jpeg_quality',QUALITIES),('matte_style',MATTES),('matte_color',COLORS),('art_filter',FILTERS)]:
        if raw.get(key) in choices:out[key]=raw[key]
    if isinstance(raw.get('force_neutral'),bool):out['force_neutral']=raw['force_neutral']
    # Largeurs persistantes, sans jamais reprendre des dimensions corrompues.
    raw_columns=raw.get('column_widths')
    if isinstance(raw_columns,dict):
        for side,count in (('tv',5),('local',4)):
            sizes=raw_columns.get(side)
            if isinstance(sizes,(list,tuple)) and len(sizes)==count and all(
                    isinstance(x,int) and not isinstance(x,bool) and 50<=x<=1600 for x in sizes):
                out['column_widths'][side]=list(sizes)
    # Chaque normalisation dispose de sa propre copie des sous-dictionnaires.
    out['column_widths']={side:list(widths) for side,widths in out['column_widths'].items()}
    return out


class Preferences:
    def __init__(self,folder):
        self.path=Path(folder)/'preferences.json'
        self.data=self.read()
    def read(self):
        if self.path.exists():
            try:return normalize(json.loads(self.path.read_text(encoding='utf-8')))
            except (OSError,ValueError,UnicodeError):pass
        return dict(DEFAULTS)
    def save(self,values):
        # Les dialogues de paramètres (anciens et nouveaux) ne doivent pas
        # effacer les largeurs de colonnes mémorisées.
        merged={**self.data,**values}
        data=normalize(merged)
        # Validation stricte en écriture : ne jamais enregistrer un champ IP invalide.
        data['ip']=valid_ip(merged.get('ip',''))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=None
        try:
            with tempfile.NamedTemporaryFile('w',dir=self.path.parent,encoding='utf-8',prefix='.preferences_',suffix='.tmp',delete=False) as f:
                tmp=Path(f.name)
                json.dump(data,f,ensure_ascii=False,indent=2)
                f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:
            if tmp is not None and tmp.exists():tmp.unlink()
        self.data=data
        return dict(data)

