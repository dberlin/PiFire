/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

type Assets = string[];
type Errortype = ("version" | "asset" | "other") | null;
type Field = string;
type Kind = string;
type Mode = string | null;
type Message = string;
type Result = "Error";
type Filename = string;
type Id = string;
type Type = string;
type Assets1 = CookFileAsset[];
type Bordercolor = string;
type Display = boolean;
type Backgroundcolor = string;
type Bordercolor1 = string;
type Color = string;
type Content = string;
type Enabled = boolean;
type Position = string;
type Type1 = string;
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
export type FiniteNumber = number;
type Backgroundcolor1 = string;
type Bordercapstyle = string;
type Bordercolor2 = string;
type Borderdash = FiniteNumber[];
type Borderjoinstyle = string;
type Data = HistoryPoint[];
type Fill = boolean;
type Hidden = boolean;
type Label = string;
type Pointbackgroundcolor = string;
type Pointbordercolor = string;
type Pointborderwidth = number;
type Pointhitradius = number;
type Pointhoverbackgroundcolor = string;
type Pointhoverbordercolor = string;
type Pointhoverborderwidth = number;
type Pointhoverradius = number;
type Pointradius = number;
type Pointstyle = string;
type Spangaps = boolean;
type ChartData = HistoryDataset[];
type TimeLabels = (FiniteNumber | string)[];
type Assets2 = string[];
type Date = string;
type Edited = string;
type Id1 = string;
type Text = string;
type Time = string;
type Assets3 = CookFileAsset[];
type Comments = CookFileComment[];
type EventTotals = CookFileTotals | EmptyCookFileTotals;
type Augerontime = string;
type Cooktime = string;
type EstusageI = string;
type EstusageM = string;
type AugerontimeC = string | 0;
type EndtimeC = string | 0;
type EstusageI1 = string;
type EstusageM1 = string;
type Id2 = string | number;
type Mode1 = string;
type StarttimeC = string | 0;
type Timeinmode = string | 0;
type Events = CookFileEvent[];
type Filename1 = string;
type Endtime = string;
type EndtimeEpoch = FiniteNumber | string;
type Id3 = string;
type Starttime = string;
type StarttimeEpoch = FiniteNumber | string;
type Thumbnail = string;
type Title = string;
type Units = string;
type Version = string;
type NewLabelSafe = string;
type Errortype1 = ("version" | "asset" | "other") | null;
type Message1 = string;
type Status = number;
type Filename2 = string;
type Thumbnail1 = string;
type Title1 = string;
type Items = FileListItem[];
type LastPage = number;
type Page = number;
type PerPage = number;
type Reverse = boolean;
type Total = number;
type Filename3 = string;
type ChartData1 = HistoryDataset[];
type Minutes = number;
type TimeLabels1 = FiniteNumber[];
type Assets4 = string[];
type Name = string;
type Quantity = string;
type Assets5 = string[];
type Ingredients = string[];
type Step = number;
type Text1 = string;
type AugerontimeC1 = string;
type EndtimeC1 = string | 0;
type EstusageI2 = string;
type EstusageM2 = string;
type FanontimeC = string | null;
type Id4 = string;
type Mode2 = string;
type PMode = number;
type PelletBrandType = string;
type SmartStartProfile = number;
type Smokeplus = boolean;
type StarttimeC1 = string;
type Timeinmode1 = string;
type Metrics = MetricRecord[];
type Units1 = "F" | "C";
type Filename4 = string;
type Id5 = string;
type Type2 = string;
type Assets6 = RecipeAsset[];
type Ingredients1 = Ingredient[];
type Instructions = Instruction[];
type Message2 = string;
type Mode3 = "Smoke" | "Hold" | "Startup" | "Shutdown";
type Notify = boolean;
type Pause = boolean;
type Food = FiniteNumber[];
type Steps = RecipeStep[];
type Assets7 = RecipeAsset[];
type Filename5 = string;
type Author = string;
type CookTime = number;
type Description = string;
type Difficulty = string;
type FoodProbes = number;
type Id6 = string;
type Image = string;
type PrepTime = number;
type Rating = number;
type Thumbnail2 = string;
type Title2 = string;
type Units2 = string;
type Username = string;
type Version1 = string;
type Assets8 = string[];
type File = string;
type Index = number;
type Section = "ingredients" | "instructions";
type Action = "add";
type File1 = string;
type Action1 = "delete";
type File2 = string;
type Index1 = number;
type Action2 = "update";
type File3 = string;
type Index2 = number;
type Name1 = string;
type Quantity1 = string;
type Action3 = "add";
type File4 = string;
type Action4 = "delete";
type File5 = string;
type Index3 = number;
type Action5 = "update";
type File6 = string;
type Index4 = number;
type Ingredients2 = string[];
type Step1 = number;
type Text2 = string;
type Author1 = string;
type CookTime1 = number;
type Description1 = string;
type Difficulty1 = string;
type FoodProbes1 = number;
type PrepTime1 = number;
type Rating1 = number;
type Title3 = string;
type Units3 = string;
type File7 = string;
type Assets9 = string[];
type File8 = string;
type Section1 = "splash";
type Action6 = "delete";
type File9 = string;
type Index5 = number;
type Action7 = "insert";
type File10 = string;
type Index6 = number;
type Action8 = "update";
type File11 = string;
type Index7 = number;

export interface PiFireContentWebContracts {
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "AssetNamesData".
 */
export interface AssetNamesData {
  assets: Assets;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "ContentErrorData".
 */
export interface ContentErrorData {
  errortype?: Errortype;
  field?: Field;
  kind?: Kind;
  mode?: Mode;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "ContentErrorEnvelope".
 */
export interface ContentErrorEnvelope {
  data: ContentErrorData | null;
  message: Message;
  result: Result;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileAsset".
 */
export interface CookFileAsset {
  filename: Filename;
  id: Id;
  type: Type;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileAssetsData".
 */
export interface CookFileAssetsData {
  assets: Assets1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileChartData".
 */
export interface CookFileChartData {
  annotations: Annotations;
  chart_data: ChartData;
  probe_mapper: HistoryProbeMapper;
  time_labels: TimeLabels;
}
interface Annotations {
  [k: string]: HistoryAnnotation | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryAnnotation".
 */
export interface HistoryAnnotation {
  borderColor: Bordercolor;
  borderWidth?: number;
  display?: Display;
  label?: HistoryAnnotationLabel | null;
  type: Type1;
  xMax: FiniteNumber;
  xMin: FiniteNumber;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryAnnotationLabel".
 */
export interface HistoryAnnotationLabel {
  backgroundColor?: Backgroundcolor;
  borderColor?: Bordercolor1;
  color?: Color;
  content?: Content;
  enabled?: Enabled;
  position?: Position;
  rotation?: number;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryDataset".
 */
export interface HistoryDataset {
  backgroundColor?: Backgroundcolor1;
  borderCapStyle?: Bordercapstyle;
  borderColor?: Bordercolor2;
  borderDash?: Borderdash;
  borderDashOffset?: number;
  borderJoinStyle?: Borderjoinstyle;
  data: Data;
  fill?: Fill;
  hidden?: Hidden;
  label: Label;
  lineTension?: number;
  pointBackgroundColor?: Pointbackgroundcolor;
  pointBorderColor?: Pointbordercolor;
  pointBorderWidth?: Pointborderwidth;
  pointHitRadius?: Pointhitradius;
  pointHoverBackgroundColor?: Pointhoverbackgroundcolor;
  pointHoverBorderColor?: Pointhoverbordercolor;
  pointHoverBorderWidth?: Pointhoverborderwidth;
  pointHoverRadius?: Pointhoverradius;
  pointRadius?: Pointradius;
  pointStyle?: Pointstyle;
  spanGaps?: Spangaps;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryPoint".
 */
export interface HistoryPoint {
  x: FiniteNumber;
  y: FiniteNumber | null;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryProbeMapper".
 */
export interface HistoryProbeMapper {
  primarysp: Primarysp;
  probes: Probes;
  targets: Targets;
  [k: string]: unknown | undefined;
}
interface Primarysp {
  [k: string]: number | undefined;
}
interface Probes {
  [k: string]: number | undefined;
}
interface Targets {
  [k: string]: number | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileComment".
 */
export interface CookFileComment {
  assets: Assets2;
  date: Date;
  edited: Edited;
  id: Id1;
  text: Text;
  time: Time;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileDetail".
 */
export interface CookFileDetail {
  assets: Assets3;
  comments: Comments;
  event_totals: EventTotals;
  events: Events;
  filename: Filename1;
  graph_labels: CookFileLabels;
  metadata: CookFileMetadata;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileTotals".
 */
export interface CookFileTotals {
  augerontime: Augerontime;
  cooktime: Cooktime;
  estusage_i: EstusageI;
  estusage_m: EstusageM;
  pellet_level_end: FiniteNumber;
  pellet_level_start: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "EmptyCookFileTotals".
 */
export interface EmptyCookFileTotals {}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileEvent".
 */
export interface CookFileEvent {
  augerontime_c: AugerontimeC;
  endtime_c: EndtimeC;
  estusage_i: EstusageI1;
  estusage_m: EstusageM1;
  id: Id2;
  mode: Mode1;
  pellet_level_end: FiniteNumber;
  pellet_level_start: FiniteNumber;
  starttime_c: StarttimeC;
  timeinmode: Timeinmode;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileLabels".
 */
export interface CookFileLabels {
  primarysp: Primarysp1;
  probes: Probes1;
  targets: Targets1;
  [k: string]: unknown | undefined;
}
interface Primarysp1 {
  [k: string]: string | undefined;
}
interface Probes1 {
  [k: string]: string | undefined;
}
interface Targets1 {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileMetadata".
 */
export interface CookFileMetadata {
  endtime: Endtime;
  endtime_epoch: EndtimeEpoch;
  id: Id3;
  starttime: Starttime;
  starttime_epoch: StarttimeEpoch;
  thumbnail: Thumbnail;
  title: Title;
  units: Units;
  version: Version;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "CookFileLabelData".
 */
export interface CookFileLabelData {
  new_label_safe: NewLabelSafe;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "FileErrorDetail".
 */
export interface FileErrorDetail {
  errortype: Errortype1;
  message: Message1;
  status: Status;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "FileListItem".
 */
export interface FileListItem {
  filename: Filename2;
  thumbnail: Thumbnail1;
  title: Title1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "FileListing".
 */
export interface FileListing {
  items: Items;
  last_page: LastPage;
  page: Page;
  per_page: PerPage;
  reverse: Reverse;
  total: Total;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "FilenameData".
 */
export interface FilenameData {
  filename: Filename3;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryChartData".
 */
export interface HistoryChartData {
  annotations: Annotations1;
  chart_data: ChartData1;
  graph_labels: HistoryGraphLabels;
  minutes: Minutes;
  probe_mapper: HistoryProbeMapper;
  time_labels: TimeLabels1;
}
interface Annotations1 {
  [k: string]: HistoryAnnotation | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "HistoryGraphLabels".
 */
export interface HistoryGraphLabels {
  primarysp: Primarysp2;
  probes: Probes2;
  targets: Targets2;
  [k: string]: unknown | undefined;
}
interface Primarysp2 {
  [k: string]: string | undefined;
}
interface Probes2 {
  [k: string]: string | undefined;
}
interface Targets2 {
  [k: string]: string | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "Ingredient".
 */
export interface Ingredient {
  assets: Assets4;
  name: Name;
  quantity: Quantity;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "Instruction".
 */
export interface Instruction {
  assets: Assets5;
  ingredients: Ingredients;
  step: Step;
  text: Text1;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "MetricRecord".
 */
export interface MetricRecord {
  auger_cycle_time: FiniteNumber;
  augerontime: FiniteNumber;
  augerontime_c: AugerontimeC1;
  endtime: FiniteNumber;
  endtime_c: EndtimeC1;
  estusage_i: EstusageI2;
  estusage_m: EstusageM2;
  fanontime: FiniteNumber;
  fanontime_c: FanontimeC;
  id: Id4;
  mode: Mode2;
  p_mode: PMode;
  pellet_brand_type: PelletBrandType;
  pellet_level_end: FiniteNumber;
  pellet_level_start: FiniteNumber;
  primary_setpoint: FiniteNumber;
  smart_start_profile: SmartStartProfile;
  smokeplus: Smokeplus;
  starttime: FiniteNumber;
  starttime_c: StarttimeC1;
  startup_temp: FiniteNumber;
  timeinmode: Timeinmode1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "MetricsPayload".
 */
export interface MetricsPayload {
  augerrate: FiniteNumber;
  metrics: Metrics;
  units: Units1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeAsset".
 */
export interface RecipeAsset {
  filename: Filename4;
  id: Id5;
  type: Type2;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeAssetsData".
 */
export interface RecipeAssetsData {
  assets: Assets6;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeBody".
 */
export interface RecipeBody {
  ingredients: Ingredients1;
  instructions: Instructions;
  steps: Steps;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStep".
 */
export interface RecipeStep {
  hold_temp: FiniteNumber;
  message: Message2;
  mode: Mode3;
  notify: Notify;
  pause: Pause;
  timer: FiniteNumber;
  trigger_temps: RecipeTriggerTemperatures;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeTriggerTemperatures".
 */
export interface RecipeTriggerTemperatures {
  food: Food;
  primary: FiniteNumber;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeDetail".
 */
export interface RecipeDetail {
  assets: Assets7;
  filename: Filename5;
  metadata: RecipeMetadata;
  recipe: RecipeBody;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeMetadata".
 */
export interface RecipeMetadata {
  author: Author;
  cook_time: CookTime;
  description: Description;
  difficulty: Difficulty;
  food_probes: FoodProbes;
  id: Id6;
  image: Image;
  prep_time: PrepTime;
  rating: Rating;
  thumbnail: Thumbnail2;
  title: Title2;
  units: Units2;
  username: Username;
  version: Version1;
  [k: string]: unknown | undefined;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeIndexedAssetAssignmentRequest".
 */
export interface RecipeIndexedAssetAssignmentRequest {
  assets: Assets8;
  file: File;
  index: Index;
  section: Section;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeIngredientAddRequest".
 */
export interface RecipeIngredientAddRequest {
  action: Action;
  file: File1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeIngredientDeleteRequest".
 */
export interface RecipeIngredientDeleteRequest {
  action: Action1;
  file: File2;
  index: Index1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeIngredientUpdateRequest".
 */
export interface RecipeIngredientUpdateRequest {
  action: Action2;
  file: File3;
  index: Index2;
  name: Name1;
  quantity: Quantity1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeInstructionAddRequest".
 */
export interface RecipeInstructionAddRequest {
  action: Action3;
  file: File4;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeInstructionDeleteRequest".
 */
export interface RecipeInstructionDeleteRequest {
  action: Action4;
  file: File5;
  index: Index3;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeInstructionUpdateRequest".
 */
export interface RecipeInstructionUpdateRequest {
  action: Action5;
  file: File6;
  index: Index4;
  ingredients: Ingredients2;
  step: Step1;
  text: Text2;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeMetadataFields".
 */
export interface RecipeMetadataFields {
  author?: Author1;
  cook_time?: CookTime1;
  description?: Description1;
  difficulty?: Difficulty1;
  food_probes?: FoodProbes1;
  prep_time?: PrepTime1;
  rating?: Rating1;
  title?: Title3;
  units?: Units3;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeMetadataUpdateRequest".
 */
export interface RecipeMetadataUpdateRequest {
  fields: RecipeMetadataFields;
  file: File7;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeSplashAssetAssignmentRequest".
 */
export interface RecipeSplashAssetAssignmentRequest {
  assets: Assets9;
  file: File8;
  section: Section1;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStepDeleteRequest".
 */
export interface RecipeStepDeleteRequest {
  action: Action6;
  file: File9;
  index: Index5;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStepInsertRequest".
 */
export interface RecipeStepInsertRequest {
  action: Action7;
  file: File10;
  index: Index6;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStepUpdateRequest".
 */
export interface RecipeStepUpdateRequest {
  action: Action8;
  file: File11;
  index: Index7;
  step: RecipeStep;
}
