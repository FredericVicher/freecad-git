# FreeCAD Git Integration

## Objectif
Workbench FreeCAD permettant une gestion de versions git efficace :
- Commit sélectif : Document.xml + .brp importés uniquement
- Détection automatique géométries calculées vs importées
- Au pull : recalcul sélectif via diff git + touch()/recompute()

## Architecture
- Workbench FreeCAD en Python
- API FreeCAD en mémoire (FreeCAD.ActiveDocument)
- **pygit2** (binding libgit2) pour les opérations git : blobs/trees/commits
  construits directement depuis des `bytes` en RAM, jamais via le working tree
- **.FCStd produit comme cache** via `doc.save()` natif de FreeCAD — pas de
  packer/unpacker custom (FreeCAD gère déjà zip + Mimetype correctement)
- Le diff Document.xml est obtenu via l'object database de git (deux blobs en
  mémoire), pas via un differ XML maison

## Points clés FreeCAD
- obj.TypeId distingue objets paramétriques vs importés
- obj.InList / obj.OutList = graphe de dépendances
- FreeCAD.ActiveDocument.Content = sérialisation XML en mémoire
- obj.touch() + doc.recompute() = recalcul sélectif
- doc.save(path) = sérialisation .FCStd native (zip + Mimetype)

## Python utilisé
C:\Program Files\FreeCAD 1.1\bin\python.exe

## État d'avancement
- [x] Détecteur calculé/importé (`freecad_git/detector.py`)
- [x] Lecture du cache `.FCStd` (`freecad_git/cache.py`)
- [x] Opérations git sélectives via pygit2 (`freecad_git/git_store.py`)
- [x] Reconciliation post-pull (`freecad_git/reconciler.py`)
- [x] Orchestration commit/pull (`freecad_git/workflow.py`)
- [x] Interface workbench GUI (`freecad_git/commands.py`, `InitGui.py`)
- [ ] Détection préventive .brp d'objets workbench-custom (à archiver défensivement)
- [ ] Package metadata pour Addon Manager (`package.xml`)

## Déploiement (dev local)
Jonction NTFS de `<freecad-git source>` vers
`%APPDATA%\FreeCAD\v1-1\Mod\freecad-git`. PySide6 est utilisé (FreeCAD 1.1).