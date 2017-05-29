import bpy
import _cycles
import os
from .OSOReader import OSOReader
from .Nodes import NodeGraph
from .NodeGroupBuilder import CreateNodeGroup


class OSLReload(bpy.types.Operator):
    bl_idname = "bpyosl.reload"
    bl_label = "Reload"

    def execute(self, context):
        # TODO: This only works if the node is the currently actice node
        context.active_node.UpdateScript()
        print("RELOAD Finished!")
        return{'FINISHED'}


class OSOAstBuilder():
    def __init__(self, oso):
        self.OSO = oso
        self.Instructions = []

    def Parse(self):
        idx = 0
        while idx < len(self.OSO.Instructions):
            # try:
            instance = self.OSO.MakeOpcode(idx)
            self.Instructions.append(instance)
            idx = instance.NextIndex()
            # except:
            #    print "Unexpected error:", sys.exc_info()[0]
            #    return False
        return True


class ShaderOSLPY(bpy.types.NodeCustomGroup):
    def my_osl_compile(self, input_path):
        """compile .osl file with given filepath to temporary .oso file"""
        import tempfile
        output_file = tempfile.NamedTemporaryFile(
            mode='w', suffix=".oso", delete=False)
        output_path = output_file.name
        output_file.close()

        ok = _cycles.osl_compile(input_path, output_path)
        print("osl compile output = %s" % output_path)
        if ok:
            print("OSL shader compilation succeeded")

        return ok, output_path

    def UpdateScript(self):
        import tempfile

        if self.ScriptType == "INTERNAL":
            # write text datablock contents to temporary file
            osl_file = tempfile.NamedTemporaryFile(
                mode='w', suffix=".osl", delete=False)
            osl_file.write(bpy.data.texts[self.script].as_string())
            osl_file.close()
            ok, oso_path = self.my_osl_compile(osl_file.name)
            os.remove(osl_file.name)
        else:
            osl_file = bpy.path.abspath(self.extScript)
            ok, oso_path = self.my_osl_compile(osl_file)

        print("Reading bytecode")
        oso = OSOReader(oso_path)
        if oso.Load():
            print("Parsing bytecode")
            ast = OSOAstBuilder(oso)
            if ast.Parse():
                graph = NodeGraph()
                graph.ShaderName = oso.ShaderName
                InputNode = graph.CreateNode("NodeGroupInput")
                InputNode.Name = "InputNode"
                InputIndex = 0
                OutputNode = graph.CreateNode("NodeGroupOutput")
                OutputNode.Name = "OutputNode"
                for var in oso.Variables:
                    if var.varType == 'const' or var.varType == 'oparam':
                        graph.MakeConst(var)
                    elif var.varType == 'param':
                        graph.SetVar(var, InputNode, InputIndex)
                        InputIndex = InputIndex + 1
                    elif var.IsArray():
                        graph.MakeArray(var, oso)

                print("Generating code...")
                for inst in ast.Instructions:
                    if inst.Tag == "___main___":
                        inst.Generate(graph)
                OutputIdx = 0
                for var in oso.Variables:
                    if var.varType == "oparam":
                        if var.Name in graph.Variables.keys():
                            graph.AddLink(OutputNode, OutputIdx, var)
                        else:
                            print("Mising var :%s" % var.Name)
                        OutputIdx = OutputIdx + 1
                print("Compiled %s", graph.ShaderName)
                print("Generating nodegroup...")
                self.node_tree = CreateNodeGroup(graph, oso.Variables)
                print("Done!")

    def mypropUpdate(self, context):
        print("new value = %s" % self.script)
        self.UpdateScript()
        return None

    bl_name = 'ShaderOSLPY'
    bl_label = 'OSLPY'
    bl_icon = 'NONE'
    script = bpy.props.StringProperty(update=mypropUpdate)
    extScript = bpy.props.StringProperty(
        subtype="FILE_PATH", update=mypropUpdate)
    ScriptType = bpy.props.EnumProperty(
        name="Script Type",
        description="Script Type",
        items=[
            ("INTERNAL", "Internal", "Internal Script"),
            ("EXTERNAL", "External", "External Script")
        ],
        update=mypropUpdate
    )

    def init(self, context):
        self.getNodetree(self.name + '_node_tree2')

    def update(self):
        pass

    # Additional buttons displayed on the node.
    def draw_buttons(self, context, layout):
        layout.label("Node settings")
        layout.prop(self, "ScriptType", expand=True)
        if self.ScriptType == "INTERNAL":
            row = layout.row(align=True)
            row.alignment = 'EXPAND'
            row.prop_search(self, "script", bpy.data, "texts")
            sub = row.row()
            sub.scale_x = 0.2
            sub.operator("bpyosl.reload")
        else:
            row = layout.row(align=True)
            row.alignment = 'EXPAND'
            row.prop(self, "extScript")
            sub = row.row()
            sub.scale_x = 0.2
            sub.operator("bpyosl.reload")

    def value_set(self, obj, path, value):
        if '.' in path:
            path_prop, path_attr = path.rsplit('.', 1)
            prop = obj.path_resolve(path_prop)
        else:
            prop = obj
            path_attr = path
        setattr(prop, path_attr, value)

    def createNodetree(self, name):
        self.node_tree = bpy.data.node_groups.new(name, 'ShaderNodeTree')
        # Nodes
        self.addNode('NodeGroupInput', {'name': 'GroupInput'})
        self.addNode('NodeGroupOutput', {'name': 'GroupOutput'})

    def getNodetree(self, name):
        if bpy.data.node_groups.find(name) == -1:
            self.createNodetree(name)
        else:
            self.node_tree = bpy.data.node_groups[name]

    def addSocket(self, is_output, sockettype, name):
        # for now duplicated socket names are not allowed
        if is_output:
            if self.node_tree.nodes['GroupOutput'].inputs.find(name) == -1:
                socket = self.node_tree.outputs.new(sockettype, name)
        else:
            if self.node_tree.nodes['GroupInput'].outputs.find(name) == -1:
                socket = self.node_tree.inputs.new(sockettype, name)
        return socket

    def addNode(self, nodetype, attrs):
        node = self.node_tree.nodes.new(nodetype)
        for attr in attrs:
            self.value_set(node, attr, attrs[attr])
        return node

    def getNode(self, nodename):
        if self.node_tree.nodes.find(nodename) > -1:
            return self.node_tree.nodes[nodename]
        return None

    def innerLink(self, socketin, socketout):
        SI = self.node_tree.path_resolve(socketin)
        SO = self.node_tree.path_resolve(socketout)
        self.node_tree.links.new(SI, SO)

    def free(self):
        if self.node_tree.users == 1:
            bpy.data.node_groups.remove(self.node_tree, do_unlink=True)