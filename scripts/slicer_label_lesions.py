# ruff: noqa
"""
3D Slicer script: показать числовые метки (label) на 3D моделях очагов.

Использование: вставить в Python-консоль Slicer (Ctrl+3) или отправить через exec API.

Находит дочерние модели под родительским узлом (Subject Hierarchy),
вычисляет центроид каждой модели и размещает markup-fiducial с текстовой меткой.
"""

# ============ НАСТРОЙКИ ============
PARENT_NODE_NAME = "master_model"  # Имя родительского узла в Subject Hierarchy
LABEL_COLOR = (1.0, 1.0, 1.0)  # Цвет текста (белый)
TEXT_SCALE = 5.0  # Размер текста (подстроить под масштаб сцены)
LABELS_NODE_NAME = "LesionLabels"  # Имя создаваемого узла с метками
# ===================================

import slicer
import vtk


def get_child_models(parent_name):
    """Найти дочерние модели через Subject Hierarchy.

    Обходит все SH-элементы с именем parent_name и возвращает
    дочерние vtkMRMLModelNode из первого элемента, содержащего модели.
    Это нужно, потому что в сцене может быть несколько элементов
    с одинаковым именем (например, сегментация + папка с моделями).
    """
    sh_node = slicer.mrmlScene.GetSubjectHierarchyNode()

    # Собрать все SH items с данным именем
    matching_items = []
    scene_item = sh_node.GetSceneItemID()
    all_items = vtk.vtkIdList()
    sh_node.GetItemChildren(scene_item, all_items, True)  # recursive=True
    for i in range(all_items.GetNumberOfIds()):
        item_id = all_items.GetId(i)
        if sh_node.GetItemName(item_id) == parent_name:
            matching_items.append(item_id)

    if not matching_items:
        raise RuntimeError(f"Элемент '{parent_name}' не найден в Subject Hierarchy")

    # Для каждого кандидата проверить, есть ли дочерние модели
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
        f"Найдено {len(matching_items)} элементов '{parent_name}', "
        f"но ни один не содержит дочерних моделей (vtkMRMLModelNode)"
    )


def get_model_centroid(model_node):
    """Вычислить центроид модели по её bounds."""
    bounds = [0.0] * 6
    model_node.GetBounds(bounds)
    return [
        (bounds[0] + bounds[1]) / 2.0,
        (bounds[2] + bounds[3]) / 2.0,
        (bounds[4] + bounds[5]) / 2.0,
    ]


def sort_key(model):
    """Сортировка: числовые имена по значению, остальные — лексикографически."""
    name = model.GetName()
    try:
        return (0, int(name))
    except ValueError:
        return (1, name)


def create_labels(models, node_name, text_scale, color):
    """Создать markup-fiducial узел с текстовыми метками в центроидах моделей."""
    # Удалить предыдущие метки
    old_node = slicer.mrmlScene.GetFirstNodeByName(node_name)
    if old_node:
        slicer.mrmlScene.RemoveNode(old_node)

    markups = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLMarkupsFiducialNode", node_name)

    # Настройка отображения: крупный текст, невидимый маркер
    dn = markups.GetDisplayNode()
    dn.SetPointLabelsVisibility(True)
    dn.SetTextScale(text_scale)
    dn.SetGlyphScale(0.0)  # Скрыть точку-маркер, показать только текст
    dn.SetSelectedColor(*color)
    dn.SetColor(*color)
    dn.SetActiveColor(*color)
    dn.SetOpacity(1.0)
    # Отключить взаимодействие — метки нельзя случайно сдвинуть
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
    raise RuntimeError(f"Не найдено дочерних моделей под '{PARENT_NODE_NAME}'")

markups = create_labels(models, LABELS_NODE_NAME, TEXT_SCALE, LABEL_COLOR)
print(f"Создано {markups.GetNumberOfControlPoints()} меток для моделей под '{PARENT_NODE_NAME}'")
