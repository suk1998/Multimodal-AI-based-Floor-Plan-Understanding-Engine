import type {Vertex} from "./roi";

export type EditorMode = "pan" | "draw" | "edit";
export interface EditorState {
  points: Vertex[];
  closed: boolean;
  drawing: boolean;
  mode: EditorMode;
  selected: number|null;
  backup: Vertex[]|null;
  interactionKey: number;
  correctionBackup: Vertex[]|null;
  gestureActive: boolean;
}
export type EditorAction =
  | {type:"start"|"pan"|"edit"|"finish"|"deselect"|"cancelDrawing"|"undo"|"reset"}
  | {type:"add";point:Vertex}
  | {type:"move";index:number;point:Vertex}
  | {type:"select";index:number|null}
  | {type:"load";points:Vertex[]}
  | {type:"rectanglePreview";points:Vertex[];closed:boolean;selected:number|null}
  | {type:"rectangleComplete";points:Vertex[];created:boolean;selected:number|null}
  | {type:"restoreGesture";state:EditorState}
  | {type:"rectangularize";points:Vertex[]}
  | {type:"cancelCorrection"};
export const initialEditor = (): EditorState => ({points:[],closed:false,drawing:false,mode:"pan",selected:null,backup:null,interactionKey:0,correctionBackup:null,gestureActive:false});

// Navigation mode and an unfinished polygon are separate state. A view command
// must never stand in for cancelling or clearing user geometry.
export function reduceEditor(state: EditorState, action: EditorAction): EditorState {
  const command={...state,interactionKey:state.interactionKey+1};
  switch(action.type) {
    case "rectanglePreview":return {...state,points:action.points.map(p=>[...p] as Vertex),closed:action.closed,selected:action.selected,gestureActive:true};
    case "rectangleComplete":return {...command,points:action.points.map(p=>[...p] as Vertex),closed:true,drawing:false,mode:"edit",selected:action.selected,backup:null,gestureActive:false,correctionBackup:action.created?null:state.correctionBackup};
    case "restoreGesture":return {...action.state,interactionKey:state.interactionKey,gestureActive:false};
    case "rectangularize":return state.closed?{...command,points:action.points.map(p=>[...p] as Vertex),mode:"edit",selected:null,gestureActive:false,correctionBackup:state.correctionBackup??state.points.map(p=>[...p] as Vertex)}:state;
    case "cancelCorrection":return state.correctionBackup?{...command,points:state.correctionBackup.map(p=>[...p] as Vertex),closed:true,drawing:false,mode:"edit",selected:null,backup:null,gestureActive:false,correctionBackup:null}:state;
    case "start":
      return state.drawing
        ? {...command,mode:"draw",selected:null}
        : {...command,points:[],closed:false,drawing:true,mode:"draw",selected:null,
           backup:state.closed?state.points.map(p=>[...p] as Vertex):null};
    case "pan":return {...command,mode:"pan",selected:null};
    case "edit":return state.closed?{...command,mode:"edit",selected:null}:state;
    case "finish":return state.drawing && state.points.length>=3
      ? {...command,closed:true,drawing:false,mode:"edit",selected:null,backup:null}:state;
    case "deselect":return {...command,selected:null};
    case "cancelDrawing":return state.drawing
      ? {...command,points:state.backup??[],closed:Boolean(state.backup),drawing:false,mode:"pan",selected:null,backup:null}:state;
    case "undo":return state.drawing?{...command,points:state.points.slice(0,-1),selected:null}:state;
    case "reset":return {...initialEditor(),interactionKey:command.interactionKey};
    case "load":return {...initialEditor(),points:action.points.map(p=>[...p] as Vertex),closed:true,mode:"edit",interactionKey:command.interactionKey};
    case "add":return state.drawing && state.mode==="draw" && state.points.length<512
      ? {...state,points:[...state.points,[...action.point] as Vertex]}:state;
    case "move":return state.closed && state.mode==="edit" && action.index>=0 && action.index<state.points.length
      ? {...state,points:state.points.map((p,i)=>i===action.index?[...action.point] as Vertex:p)}:state;
    case "select":return {...state,selected:state.closed && state.mode==="edit" && action.index!==null && action.index>=0 && action.index<state.points.length?action.index:null};
  }
}
