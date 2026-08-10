/* eslint-disable */
// GENERATED from Pydantic web contracts — do not edit. Regenerate: bun run gen:types

export type Assets = string[];
export type Errortype = ("version" | "asset" | "other") | null;
export type Field = string;
export type Kind = string;
export type Mode = string | null;
export type Message = string;
export type Result = "Error";
export type Filename = string;
export type Id = string;
export type Type = string;
export type Assets1 = CookFileAsset[];
export type Bordercolor = string;
export type Display = boolean;
export type Backgroundcolor = string;
export type Bordercolor1 = string;
export type Color = string;
export type Content = string;
export type Enabled = boolean;
export type Position = string;
export type Type1 = string;
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "FiniteNumber".
 */
export type FiniteNumber = number;
export type Backgroundcolor1 = string;
export type Bordercapstyle = string;
export type Bordercolor2 = string;
export type Borderdash = FiniteNumber[];
export type Borderjoinstyle = string;
export type Data = HistoryPoint[];
export type Fill = boolean;
export type Hidden = boolean;
export type Label = string;
export type Pointbackgroundcolor = string;
export type Pointbordercolor = string;
export type Pointborderwidth = number;
export type Pointhitradius = number;
export type Pointhoverbackgroundcolor = string;
export type Pointhoverbordercolor = string;
export type Pointhoverborderwidth = number;
export type Pointhoverradius = number;
export type Pointradius = number;
export type Pointstyle = string;
export type Spangaps = boolean;
export type ChartData = HistoryDataset[];
export type TimeLabels = (FiniteNumber | string)[];
export type Assets2 = string[];
export type Date = string;
export type Edited = string;
export type Id1 = string;
export type Text = string;
export type Time = string;
export type Assets3 = CookFileAsset[];
export type Comments = CookFileComment[];
export type EventTotals = CookFileTotals | EmptyCookFileTotals;
export type Augerontime = string;
export type Cooktime = string;
export type EstusageI = string;
export type EstusageM = string;
export type AugerontimeC = string | 0;
export type EndtimeC = string | 0;
export type EstusageI1 = string;
export type EstusageM1 = string;
export type Id2 = string | number;
export type Mode1 = string;
export type StarttimeC = string | 0;
export type Timeinmode = string | 0;
export type Events = CookFileEvent[];
export type Filename1 = string;
export type Endtime = string;
export type EndtimeEpoch = FiniteNumber | string;
export type Id3 = string;
export type Starttime = string;
export type StarttimeEpoch = FiniteNumber | string;
export type Thumbnail = string;
export type Title = string;
export type Units = string;
export type Version = string;
export type NewLabelSafe = string;
export type Errortype1 = ("version" | "asset" | "other") | null;
export type Message1 = string;
export type Status = number;
export type Filename2 = string;
export type Thumbnail1 = string;
export type Title1 = string;
export type Items = FileListItem[];
export type LastPage = number;
export type Page = number;
export type PerPage = number;
export type Reverse = boolean;
export type Total = number;
export type Filename3 = string;
export type ChartData1 = HistoryDataset[];
export type Minutes = number;
export type TimeLabels1 = FiniteNumber[];
export type Assets4 = string[];
export type Name = string;
export type Quantity = string;
export type Assets5 = string[];
export type Ingredients = string[];
export type Step = number;
export type Text1 = string;
export type AugerontimeC1 = string;
export type EndtimeC1 = string | 0;
export type EstusageI2 = string;
export type EstusageM2 = string;
export type FanontimeC = string | null;
export type Id4 = string;
export type Mode2 = string;
export type PMode = number;
export type PelletBrandType = string;
export type SmartStartProfile = number;
export type Smokeplus = boolean;
export type StarttimeC1 = string;
export type Timeinmode1 = string;
export type Metrics = MetricRecord[];
export type Units1 = "F" | "C";
export type Filename4 = string;
export type Id5 = string;
export type Type2 = string;
export type Assets6 = string[];
export type File = string;
export type Index = number | null;
export type Section = "splash" | "ingredients" | "instructions";
export type Assets7 = RecipeAsset[];
export type Ingredients1 = Ingredient[];
export type Instructions = Instruction[];
export type Message2 = string;
export type Mode3 = "Smoke" | "Hold" | "Startup" | "Shutdown";
export type Notify = boolean;
export type Pause = boolean;
export type Food = FiniteNumber[];
export type Steps = RecipeStep[];
export type Assets8 = RecipeAsset[];
export type Filename5 = string;
export type Author = string;
export type CookTime = number;
export type Description = string;
export type Difficulty = string;
export type FoodProbes = number;
export type Id6 = string;
export type Image = string;
export type PrepTime = number;
export type Rating = number;
export type Thumbnail2 = string;
export type Title2 = string;
export type Units2 = string;
export type Username = string;
export type Version1 = string;
export type Action = "add";
export type File1 = string;
export type Action1 = "delete";
export type File2 = string;
export type Index1 = number;
export type Action2 = "update";
export type File3 = string;
export type Index2 = number;
export type Name1 = string;
export type Quantity1 = string;
export type Action3 = "add";
export type File4 = string;
export type Action4 = "delete";
export type File5 = string;
export type Index3 = number;
export type Action5 = "update";
export type File6 = string;
export type Index4 = number;
export type Ingredients2 = string[];
export type Step1 = number;
export type Text2 = string;
export type Author1 = string;
export type CookTime1 = number;
export type Description1 = string;
export type Difficulty1 = string;
export type FoodProbes1 = number;
export type PrepTime1 = number;
export type Rating1 = number;
export type Title3 = string;
export type Units3 = string;
export type File7 = string;
export type Action6 = "delete";
export type File8 = string;
export type Index5 = number;
export type Action7 = "insert";
export type File9 = string;
export type Index6 = number;
export type Action8 = "update";
export type File10 = string;
export type Index7 = number;

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
export interface Annotations {
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
export interface Primarysp {
  [k: string]: number | undefined;
}
export interface Probes {
  [k: string]: number | undefined;
}
export interface Targets {
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
export interface Primarysp1 {
  [k: string]: string | undefined;
}
export interface Probes1 {
  [k: string]: string | undefined;
}
export interface Targets1 {
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
export interface Annotations1 {
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
export interface Primarysp2 {
  [k: string]: string | undefined;
}
export interface Probes2 {
  [k: string]: string | undefined;
}
export interface Targets2 {
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
 * via the `definition` "RecipeAssetAssignmentRequest".
 */
export interface RecipeAssetAssignmentRequest {
  assets: Assets6;
  file: File;
  index?: Index;
  section: Section;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeAssetsData".
 */
export interface RecipeAssetsData {
  assets: Assets7;
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
  assets: Assets8;
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
 * via the `definition` "RecipeStepDeleteRequest".
 */
export interface RecipeStepDeleteRequest {
  action: Action6;
  file: File8;
  index: Index5;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStepInsertRequest".
 */
export interface RecipeStepInsertRequest {
  action: Action7;
  file: File9;
  index: Index6;
}
/**
 * This interface was referenced by `PiFireContentWebContracts`'s JSON-Schema
 * via the `definition` "RecipeStepUpdateRequest".
 */
export interface RecipeStepUpdateRequest {
  action: Action8;
  file: File10;
  index: Index7;
  step: RecipeStep;
}
