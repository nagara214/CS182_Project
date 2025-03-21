"""
/public/sist/home/hongmt2022/MyWorks/kaggle-bv/kaggle/working/checkpoints/Unet_wzx_pureBCE/Unet_without_kidney2.pt
/public/sist/home/hongmt2022/MyWorks/kaggle-bv/kaggle/working/checkpoints/Unet_2024-01-01_00-30-22_Dice_With_Surface/epoch_14.pt
"""
import torch as tc 
import torch.nn as nn  
import numpy as np
from tqdm import tqdm
from torch.cuda.amp import autocast
import cv2
import os,sys
from glob import glob
import matplotlib.pyplot as plt
import pandas as pd
import segmentation_models_pytorch as smp
#import cc3d
from torch.utils.data import Dataset, DataLoader
from torch.nn import DataParallel
from transformers import AutoImageProcessor, ConvNextV2Model
from transformers import ConvNextV2Config
class CFG:
# ============== model CFG =============
    model_name = 'Unet'
    #backbone = 'efficientnet-b0'
    backbone = 'se_resnet50'# convnext 卷积网络改成swin transformer 

    in_chans = 3 # 65
    # ============== training CFG =============
    image_size = 224
    input_size=224
    tile_size = image_size
    stride = tile_size // 2
    drop_egde_pixel=0
    
    target_size = 1
    # ============== fold =============#/public/sist/home/hongmt2022/MyWorks/kaggle-bv/kaggle/working/checkpoints/Unet_2023-12-30_18-55-29_Dice_With_Surface)/epoch_19.pt
    valid_id = 1#/public/sist/home/hongmt2022/MyWorks/kaggle-bv/kaggle/working/checkpoints/Unet_2023-12-29_00-22-40_BCE_with_dice)/epoch_19.pt
    batch=128 #/public/sist/home/hongmt2022/MyWorks/kaggle-bv/kaggle/working/checkpoints/Unet_2023-12-28_23-30-31_Pure_Dice/epoch_49.pt
    th_percentile = 0.004#0.005 /public/sist/home/hongmt2022/MyWorks/kaggle-bv/kaggle/input/blood-vessel-segmentation/train/kidney_2
    model_path=["/public/sist/home/hongmt2022/MyWorks/kaggle-bv/models/ConvNext/epoch_19.pt"]
    test_data_path="./kaggle/input/blood-vessel-segmentation/train/kidney_2"
    submit_test_data_path="./kaggle/input/blood-vessel-segmentation/test/*"
    submit_justify_file_path = "./kaggle/input/blood-vessel-segmentation/test/kidney_5/images/*.tif"
    kaggle = False
    
class CustomModel(nn.Module):
    def __init__(self, CFG, weight=None):
        super().__init__()
        self.CFG = CFG
        self.encoder = smp.Unet(
            encoder_name=CFG.backbone, 
            encoder_weights=weight,
            in_channels=CFG.in_chans,
            classes=CFG.target_size,
            activation=None,
        )

    def forward(self, image):
        output = self.encoder(image)
        # output = output.squeeze(-1)
        return output[:,0]#.sigmoid()


def build_model(weight=None):
    from dotenv import load_dotenv
    load_dotenv()

    print('model_name', CFG.model_name)
    print('backbone', CFG.backbone)

    model = CustomModel(CFG, weight)

    return model


def rle_encode(mask):
    pixel = mask.flatten()
    pixel = np.concatenate([[0], pixel, [0]])
    run = np.where(pixel[1:] != pixel[:-1])[0] + 1
    run[1::2] -= run[::2]
    rle = ' '.join(str(r) for r in run)
    if rle == '':
        rle = '1 1'
    return rle

def load_img(paths):
    output = []
    for path in paths:
        if path is None:
            output.append(None)
            continue
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        img = img.astype('float32') # original is uint16
        output.append(img)
    shape=[x.shape for x in output if x is not None][0]
    for i in range(len(output)):
        if output[i] is None:
            output[i] = tc.randn(shape)
    output=np.stack(output, axis=0)
    return tc.from_numpy(output)

def min_max_normalization(x:tc.Tensor)->tc.Tensor:
    """input.shape=(batch,f1,...)"""
    shape=x.shape
    if x.ndim>2:
        x=x.reshape(x.shape[0],-1)
    
    min_=x.min(dim=-1,keepdim=True)[0]
    max_=x.max(dim=-1,keepdim=True)[0]
    if min_.mean()==0 and max_.mean()==1:
        return x.reshape(shape)
    
    x=(x-min_)/(max_-min_+1e-9)
    return x.reshape(shape)

class Data_loader(Dataset):
    def __init__(self,path,s="/images/"):
        self.paths=glob(path+f"{s}*.tif")
        self.paths.sort()
        self.bool=s=="/labels/"
    
    def __len__(self):
        return len(self.paths)
    
    def __getitem__(self,index):
        img=cv2.imread(self.paths[index],cv2.IMREAD_GRAYSCALE)
        img=tc.from_numpy(img)
        if self.bool:
            img=img.to(tc.bool)
        else:
            img=img.to(tc.uint8)
        return img

def load_data(path,s):
    data_loader=Data_loader(path,s)
    data_loader=DataLoader(data_loader, batch_size=4, num_workers=0)
    data=[]
    for x in tqdm(data_loader):
        data.append(x)
    return tc.cat(data,dim=0)    

class Pipeline_Dataset(Dataset):
    def __init__(self,x,path,labels=None):
        self.img_paths  = glob(path+"/images/*")
        self.img_paths.sort()
        #assert int(self.img_paths[-1].split("/")[-1][:-4])+1==len(x)
        self.debug=labels
        self.in_chan = CFG.in_chans
        z=tc.zeros(self.in_chan//2,*x.shape[1:],dtype=x.dtype)
        self.x=tc.cat((z,x,z),dim=0)
        self.labels=labels
        
    def __len__(self):
        return self.x.shape[0]-4
    
    def __getitem__(self, index):
        x  = self.x[index:index+self.in_chan]
        if self.labels is not None :
            label=self.labels[index]
        else:
            label=tc.zeros_like(x[0])
        #Normalization
        # id=self.img_paths[index].split("/")[-3:]
        # id.pop(1)
        # id="_".join(id)
        #return img,tc.from_numpy(mask),id
        # ,id[:-4]
        return x,label

def add_edge(x:tc.Tensor,edge:int):
    #x=(C,H,W)
    #output=(C,H+2*edge,W+2*edge)
    x=tc.cat([x,tc.ones([x.shape[0],edge,x.shape[2]],dtype=x.dtype,device=x.device)*128],dim=1)
    x=tc.cat([x,tc.ones([x.shape[0],x.shape[1],edge],dtype=x.dtype,device=x.device)*128],dim=2)
    x=tc.cat([tc.ones([x.shape[0],edge,x.shape[2]],dtype=x.dtype,device=x.device)*128,x],dim=1)
    x=tc.cat([tc.ones([x.shape[0],x.shape[1],edge],dtype=x.dtype,device=x.device)*128,x],dim=2)
    return x

def TTA(x:tc.Tensor,model:nn.Module,batch=CFG.batch):
    x=x.to(tc.float32)
    x=min_max_normalization(x)
    #x.shape=(batch,c,h,w)
    if CFG.input_size!=CFG.image_size:
        x=nn.functional.interpolate(x,size=(CFG.input_size,CFG.input_size),mode='bilinear',align_corners=True)
    
    shape=x.shape
    x=[x,*[tc.rot90(x,k=i,dims=(-2,-1)) for i in range(1,4)]]
    x=tc.cat(x,dim=0)
    with autocast():
        with tc.no_grad():
            #x=[model((x[i*batch:(i+1)*batch],print(x[i*batch:(i+1)*batch].shape))[0]) for i in range(x.shape[0]//batch+1)]
            x=[model(x[i*batch:(i+1)*batch]).logits for i in range(x.shape[0]//batch+1)]
            # batch=64,64...48
            x=tc.cat(x,dim=0)
    x = x[:,CFG.in_chans//2,:,:]
    x=x.sigmoid()
    x=x.reshape(4,shape[0],*shape[2:])
    x=[tc.rot90(x[i],k=-i,dims=(-2,-1)) for i in range(4)]
    x=tc.stack(x,dim=0).mean(0)
    
    if CFG.input_size!=CFG.image_size:
        x=nn.functional.interpolate(x[None],size=(CFG.image_size,CFG.image_size),mode='bilinear',align_corners=True)[0]
    return x

configuration = ConvNextV2Config()
model = ConvNextV2Model(configuration)
model = ConvNextV2Model.from_pretrained("facebook/convnextv2-tiny-1k-224")
model = DataParallel(model)
model.load_state_dict(tc.load(CFG.model_path[0],"cpu"))
model = model.cuda()



def predict_slice(axis, x, model, cfg,path, use_tta=True):
    # Axis: 0, 1, or 2 corresponding to D, H, W
    if axis == 0:
        x = x
    elif axis == 1:
        x = x.permute(2, 0, 1)
    elif axis == 2:
        x = x.permute(1, 2, 0)
    result = np.zeros(x.shape, dtype=np.uint8)
    print(x.shape)
    dataset=Pipeline_Dataset(x,path,None)
    dataloader=DataLoader(dataset,batch_size=1,shuffle=False,num_workers=0)
    iter = 0
    for img,label in tqdm(dataloader):
        #print(label.shape)
        #img=(C,H,W)
        img=img.cuda()
        label=label.cuda()
        img=add_edge(img[0],CFG.tile_size//2)[None]
        label=add_edge(label,CFG.tile_size//2)
        x1_list = np.arange(0, label.shape[-2]-CFG.tile_size+1, CFG.stride)
        y1_list = np.arange(0, label.shape[-1]-CFG.tile_size+1, CFG.stride)

        mask_pred = tc.zeros_like(label,dtype=tc.float32,device=label.device)
        mask_count = tc.zeros_like(label,dtype=tc.float32,device=label.device)

        indexs=[]
        chip=[]
        for y1 in y1_list:
            for x1 in x1_list:
                x2 = x1 + CFG.tile_size
                y2 = y1 + CFG.tile_size
                indexs.append([x1+CFG.drop_egde_pixel,x2-CFG.drop_egde_pixel,
                                y1+CFG.drop_egde_pixel,y2-CFG.drop_egde_pixel])
                chip.append(img[...,x1:x2,y1:y2])

        y_preds = TTA(tc.cat(chip),model)
        if CFG.drop_egde_pixel:
            y_preds=y_preds[...,CFG.drop_egde_pixel:-CFG.drop_egde_pixel,
                                CFG.drop_egde_pixel:-CFG.drop_egde_pixel]
        for i,(x1,x2,y1,y2) in enumerate(indexs):
            mask_pred[...,x1:x2, y1:y2] += y_preds[i]
            mask_count[...,x1:x2, y1:y2] += 1

        mask_pred /= mask_count

            
        #Rrecover
        mask_pred=mask_pred[...,CFG.tile_size//2:-CFG.tile_size//2,CFG.tile_size//2:-CFG.tile_size//2]
        label=label[...,CFG.tile_size//2:-CFG.tile_size//2,CFG.tile_size//2:-CFG.tile_size//2]
        

            
        # result.append(((mask_pred*255).to(tc.uint8).cpu().numpy()[0]))
        result[iter] = (mask_pred*255).to(tc.uint8).cpu().numpy()[0]
        iter += 1
    if axis == 0:
        result = result
    elif axis == 1:
        result = np.transpose(result, axes = (1,2,0))
    elif axis == 2:
        result = np.transpose(result, axes = (2,0,1))

    return result

    
def get_output(debug=False):
    # ... (Your setup code) ...
    outputs=[]
    if debug:
        paths=[CFG.test_data_path]
    else:
        paths=glob(CFG.submit_test_data_path)
    for path in paths[:100]:
        # X will have shape [D, H, W] after loading and any required preprocessing
        x=load_data(path,"/images/")
        predict_d = predict_slice(0, x, model, CFG, path=path, use_tta=True)
        predict_h = predict_slice(1, x, model, CFG, path=path, use_tta=True)
        predict_w = predict_slice(2, x, model, CFG, path=path, use_tta=True)

        # Consolidate predictions from all axes (assuming simple averaging)
        # Ensure that these are appropriately weighted/combined if different strategies are used
        consolidate_pred = (predict_d + predict_h + predict_w) / 3.0
        
        # ... (Any post-processing and addition to outputs) ...
        outputs.append(consolidate_pred)

    return outputs


def get_output(debug=False):
    # ... (Your setup code) ...
    outputs=[]
    if debug:
        paths=[CFG.test_data_path]
    else:
        paths=glob(CFG.submit_test_data_path)
    for i in range(3):
        for path in paths[:100]:
            x = load_data(path,"/images/")
            if i == 0:
                predict_d = predict_slice(0, x, model, CFG, path=path, use_tta=True)
                predict_d = np.array(predict_d)
            elif i == 1:
                predict_h = predict_slice(1, x, model, CFG, path=path, use_tta=True)
                predict_h = np.array(predict_h)
            elif i == 2:
                predict_w = predict_slice(2, x, model, CFG, path=path, use_tta=True)
                predict_w = np.array(predict_w)

    consolidate_pred = (predict_d + predict_h + predict_w) / 3.0
    
    return consolidate_pred


def get_date_time():
    import datetime
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    current_time = datetime.datetime.now().strftime("%H-%M-%S")
    return current_date, current_time

if not CFG.kaggle:
    current_date, current_time = get_date_time()
 
    print("当前日期为：", current_date)
    print("当前时间为：", current_time)

is_submit=len(glob(CFG.submit_justify_file_path))!=3
debug = not is_submit  # 如果文件夹中的文件数为3，则is_submit = False，启动debug？哦我草我懂了，这是应付kaggle那个机制
print(len(glob(CFG.submit_justify_file_path)))
print('submit:',end = '')
print(is_submit)


print('start to get output')
outputs=get_output(debug) 
print('get output done')


import re

if debug:
    paths=[CFG.test_data_path]
else:
    paths=glob(CFG.submit_test_data_path)
TH = [output.flatten() for output in outputs] 
TH = np.concatenate(TH)
index = -int(len(TH) * CFG.th_percentile)
TH = np.partition(TH, index)[index]
print(TH)
submission_df = pd.DataFrame(columns=['id', 'rle'])

if debug:
    label_paths = [CFG.test_data_path + "/images"]
else:
    label_paths = glob(CFG.submit_test_data_path + "/images")
# 列出文件夹中的所有文件，并进行排序，确保顺序与outputs一致
file_list = []
print(label_paths)
for label_path in label_paths:
    kidney_id = re.split(r'[\\/]', label_path)[-2]
    file_names = sorted(os.listdir(label_path))
    for file_name in file_names:
        file_name = kidney_id + '_' + file_name
        file_list.append(file_name) 



print(file_list)


for idx, mask_pred in enumerate(outputs):
    # 获取id (例如：'kidney_1_test_label.png' -> 'kidney_1_test')
    # print(file_list)
    file_name = file_list[idx]  # idx 对应当前mask_pred的索引
    id = "_".join(file_name.split(".")[:-1])  # 移除文件扩展名部分

    # 应用阈值并执行RLE编码
    mask_pred_binary = mask_pred >= TH
    rle = rle_encode(mask_pred_binary)
    
    # 将结果追加至submission_df
    submission_row = {'id': id, 'rle': rle}
    
    # 将新行追加到submission_df DataFrame
    submission_df = pd.concat([submission_df, pd.DataFrame(submission_row, index = [0])], axis=0, ignore_index=True)



if not CFG.kaggle:
    output_path = './data/predictions/prediction-ConvNext-bce' + current_date +'-'+ current_time+ '.csv'
    submission_df.to_csv('./data/predictions/prediction-ConvNext-bce' + current_date +'-'+ current_time+ '.csv', index=False)
if CFG.kaggle:
    submission_df.to_csv('submission.csv', index=False)
    
from utils.dataset import load_data
from utils.utils import read_rle_from_path, read_images, three_dimension_dice_score, read_kidney_2_label
import surface_distance
import numpy as np

def calculate_1_ratio(matrix):
    total_elements = matrix.size
    ones_count = np.count_nonzero(matrix != 0)
    ratio = ones_count / total_elements
    return ratio

def compute_3d_surface_dice_loss(label_path, pred_path,spacing_mm ):
    label = read_kidney_2_label()
    label = label.astype(bool)
    print('label load done')
    pred = read_rle_from_path(pred_path, label.shape[1], label.shape[2])
    pred = pred.astype(bool)
    one_ratio = calculate_1_ratio(pred)
    print('one_ratio',end = '')
    print(one_ratio)
    print('pred load done')
    three_d_dice_score = three_dimension_dice_score(pred, label)
    print('three_dimension_dice_score:',end = '')
    print(three_d_dice_score)
    surface_distances = surface_distance.compute_surface_distances(label, pred, spacing_mm)
    print('surface_distance compute done')
    score = surface_distance.compute_surface_dice_at_tolerance(surface_distances, tolerance_mm=0)
    return three_d_dice_score, score

pred_path = output_path
label_path = 'kaggle/input/blood-vessel-segmentation/train/kidney_2/labels/'
spacing_mm = [1, 1, 1]
three_d_dice_score, score = compute_3d_surface_dice_loss(label_path, pred_path, spacing_mm)
print('3d_surface_dice_score:', score)