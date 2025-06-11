from typing import Literal
import os 

import slicer

EditorEffectsNames = Literal['Paint', 'Erase']

class EditorEffect:
    def __init__(self, name: EditorEffectsNames):
        self.name = name
    def activate(self):


class Slicer:
    slicer = slicer
    scene = slicer.mrmlScene
    layout = slicer.app.layoutManager()
    def __init__(self, working_folder):
        self.working_folder = working_folder
        self.main_img = ...
        self.segmentations = {}


    def load_segmentation(self, path:str, name: str | None = None)-> None:
        name = name if name else path.split('.')[0]
        segmentation_node = self.slicer.util.loadSegmentation(os.path.join(self.working_folder,path))
        segmentation_node.SetReferenceImageGeometryParameterFromVolumeNode(self.main_img)
        segmentation_node.CreateDefaultDisplayNodes()
        self.segmentations[name] = segmentation_node