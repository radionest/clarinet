# ruff: noqa
"""
3D Slicer script: show numeric text labels on the 3D models of defects.

Usage: paste into the Slicer Python console (Ctrl+3) or send via the exec API.

Finds the child models under a parent node (Subject Hierarchy), computes each
model's centroid, and places a markup-fiducial with a text label there.
"""

# ============ SETTINGS ============
PARENT_NODE_NAME = "master_model"  # Parent node name in the Subject Hierarchy
LABEL_COLOR = (1.0, 1.0, 1.0)  # Text color (white)
TEXT_SCALE = 5.0  # Text size (adjust to the scene scale)
LABELS_NODE_NAME = "ModelLabels"  # Name of the created label node
# ==================================

import slicer
import vtk


def get_child_models(parent_name):
    """Find child models via the Subject Hierarchy.

    Walks every SH item named parent_name and returns the child
    vtkMRMLModelNodes from the first item that contains models. Needed
    because a scene may hold several items with the same name (e.g. a
    segmentation plus a folder of models).
    """
    sh_node = slicer.mrmlScene.GetSubjectHierarchyNode()

    # Collect all SH items with this name
    matching_items = []
    scene_item = sh_node.GetSceneItemID()
    all_items = vtk.vtkIdList()
    sh_node.GetItemChildren(scene_item, all_items, True)  # recursive=True
    for i in range(all_items.GetNumberOfIds()):
        item_id = all_items.GetId(i)
        if sh_node.GetItemName(item_id) == parent_name:
            matching_items.append(item_id)

    if not matching_items:
        raise RuntimeError(f"Item '{parent_name}' not found in Subject Hierarchy")

    # For each candidate, check whether it has child models
    for parent_item in matching_items:
        child_ids = vtk.vtkIdList()
        sh_node.GetItemChildren(parent_item, child_ids)
        models = []
        for j in range(child_ids.GetNumberOfIds()):
            node = sh_node.GetItemDataNode(child_ids.GetId(j))
            if node and node.IsA("vtkMRMLModelNode"):
                models.append(node)
        if models:
            return models

    raise RuntimeError(
        f"Found {len(matching_items)} items '{parent_name}', "
        f"but none contains child models (vtkMRMLModelNode)"
    )


def get_model_centroid(model_node):
    """Compute a model's centroid from its bounds."""
    bounds = [0.0] * 6
    model_node.GetBounds(bounds)
    return [
        (bounds[0] + bounds[1]) / 2.0,
        (bounds[2] + bounds[3]) / 2.0,
        (bounds[4] + bounds[5]) / 2.0,
    ]


def sort_key(model):
    """Sort: numeric names by value, the rest lexicographically."""
    name = model.GetName()
    try:
        return (0, int(name))
    except ValueError:
        return (1, name)


def create_labels(models, node_name, text_scale, color):
    """Create a markup-fiducial node with text labels at the model centroids."""
    # Remove previous labels
    old_node = slicer.mrmlScene.GetFirstNodeByName(node_name)
    if old_node:
        slicer.mrmlScene.RemoveNode(old_node)

    markups = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", node_name)

    # Display settings: large text, invisible marker
    dn = markups.GetDisplayNode()
    dn.SetPointLabelsVisibility(True)
    dn.SetTextScale(text_scale)
    dn.SetGlyphScale(0.0)  # Hide the point marker, show text only
    dn.SetSelectedColor(*color)
    dn.SetColor(*color)
    dn.SetActiveColor(*color)
    dn.SetOpacity(1.0)
    # Disable interaction so labels cannot be dragged accidentally
    dn.SetHandlesInteractive(False)

    models_sorted = sorted(models, key=sort_key)
    for model in models_sorted:
        centroid = get_model_centroid(model)
        label = model.GetName()
        markups.AddControlPoint(centroid[0], centroid[1], centroid[2], label)

    markups.SetLocked(True)
    return markups


# ============ MAIN ============
models = get_child_models(PARENT_NODE_NAME)
if not models:
    raise RuntimeError(f"No child models found under '{PARENT_NODE_NAME}'")

markups = create_labels(models, LABELS_NODE_NAME, TEXT_SCALE, LABEL_COLOR)
print(f"Created {markups.GetNumberOfControlPoints()} labels for models under '{PARENT_NODE_NAME}'")
