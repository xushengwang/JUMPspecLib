import pandas as pd
import pyteomics
from pyteomics import mass
from pyteomics import mzxml
import numpy as np
import os, sys
from collections import Counter
import pickle

import re

import rpy2.robjects as ro
from rpy2.robjects.vectors import FloatVector
from elutionCases import *


class progressBar:
    def __init__(self, total):
        self.total = total
        self.barLength = 20
        self.count = 0
        self.progress = 0
        self.block = 0
        self.status = ""

    def increment(self, nIncrement=None):
        if nIncrement == None:
            self.count += 1
        else:
            self.count = nIncrement
        self.progress = self.count / self.total
        self.block = int(round(self.barLength * self.progress))
        if self.progress == 1:
            self.status = "Done...\r\n"
        else:
            self.status = ""
        #         self.status = str(self.count) + "/" + str(self.total)
        text = "\r  Progress: [{0}] {1}% {2}".format("#" * self.block + "-" * (self.barLength - self.block),
                                                     int(self.progress * 100), self.status)
        sys.stdout.write(text)
        sys.stdout.flush()


def loess():
    rstring = """
    loess.as = function(x, y, degree = 1, criterion="aicc", family="gaussian", user.span=NULL, plot=FALSE, ...) {

        criterion <- match.arg(criterion)
        family <- match.arg(family)
        x <- as.matrix(x)

        if ((ncol(x) != 1) & (ncol(x) != 2)) stop("The predictor 'x' should be one or two dimensional!!")
        if (!is.numeric(x)) stop("argument 'x' must be numeric!")
        if (!is.numeric(y)) stop("argument 'y' must be numeric!")
        if (any(is.na(x))) stop("'x' contains missing values!")
        if (any(is.na(y))) stop("'y' contains missing values!")
        if (!is.null(user.span) && (length(user.span) != 1 || !is.numeric(user.span))) 
            stop("argument 'user.span' must be a numerical number!")
        if(nrow(x) != length(y)) stop("'x' and 'y' have different lengths!")
        if(length(y) < 3) stop("not enough observations!")

        data.bind <- data.frame(x=x, y=y)
        if (ncol(x) == 1) {
            names(data.bind) <- c("x", "y")
        } else { names(data.bind) <- c("x1", "x2", "y") }

        opt.span <- function(model, criterion=c("aicc", "gcv"), span.range=c(.05, .95)){	
            as.crit <- function (x) {
                span <- x$pars$span
                traceL <- x$trace.hat
                sigma2 <- sum(x$residuals^2 ) / (x$n-1)
                aicc <- log(sigma2) + 1 + 2* (2*(traceL+1)) / (x$n-traceL-2)
                gcv <- x$n*sigma2 / (x$n-traceL)^2
                result <- list(span=span, aicc=aicc, gcv=gcv)
                return(result)
            }
            criterion <- match.arg(criterion)
            fn <- function(span) {
                mod <- update(model, span=span)
                as.crit(mod)[[criterion]]
            }
            result <- optimize(fn, span.range)
            return(list(span=result$minimum, criterion=result$objective))
        }

        control = loess.control(surface = "direct")
        if (ncol(x)==1) {
            if (is.null(user.span)) {
                fit0 <- loess(y ~ x, degree=degree, family=family, data=data.bind, control=control, ...)
                span1 <- opt.span(fit0, criterion=criterion)$span
            } else {
                span1 <- user.span
            }		
            fit <- loess(y ~ x, degree=degree, span=span1, family=family, data=data.bind, control=control, ...)
        } else {
            if (is.null(user.span)) {
                fit0 <- loess(y ~ x1 + x2, degree=degree,family=family, data.bind, control=control, ...)
                span1 <- opt.span(fit0, criterion=criterion)$span
            } else {
                span1 <- user.span
            }		
            fit <- loess(y ~ x1 + x2, degree=degree, span=span1, family=family, data=data.bind, control=control...)
        }
        return(fit)
    }
    """
    return ro.r(rstring)


def mkdir(outputFolder):
    #create search output directory
    cmdDir = "mkdir "+outputFolder
    try:
        os.system(cmdDir)
    except:
        write_log ("Directory exist")


def mzxml_2_df(mzxml):
    x1 = pyteomics.mzxml.read(mzxml)  #reading mzXML file using pyteomics
    df = pd.DataFrame([x for x in x1])  #dataframe of the mzXML file
    return df



def getMs2ToSurvey(mzxml):
    df = mzxml_2_df(mzxml)
    scans = list(df["num"])
    mslevel = list(df["msLevel"])
    rt = list(df["retentionTime"])
    
    all_ms1_scans = []
    
    print("  Read a mzxml file dataframe: to find survey scans of MS2 scans")
    res = {}
    rt_dict = {}
    for index,scan in enumerate(scans):
        rt_dict[int(scan)] = float(rt[index])
        if mslevel[index] == 1:
            survey = int(scan)
            all_ms1_scans.append(survey)
        elif mslevel[index] == 2:
            res[int(scan)] = survey
#     print("  Done ...\n")
    return res,rt_dict,all_ms1_scans, df




def parse_idtxt(idtxt):
    psms = pd.read_csv(idtxt, skiprows=1, sep=";")  # Note that ID.txt file is delimited by semicolon
    psms = psms[["Peptide", "Outfile", "XCorr","measuredMH", "calcMH"]]
    #sort by XCorr 

    psms["XCorr"] = psms["XCorr"].astype("float")
    psms.sort_values(by=["XCorr"], ascending=False)


#     psms = psms.loc[psms["Outfile"].str.contains(mzXMLBaseName)]    # Extract PSMs from FTLD_Batch2_F50.mzXML
    psms["charge"] = [outfile.split("/")[-1].split(".")[-2] for outfile in psms["Outfile"]]
    psms = psms.drop_duplicates()
    

    # Unique key is peptide-charge pair
    print("  RT of every identified peptide is being inferred and assigned")
    keys = psms["Peptide"] + "_" + psms["charge"]
    psms["keys"] = keys

    return psms



def get_df_rt_tol(dfMz, rt_lower, rt_higher):
    df = dfMz.loc[dfMz["msLevel"] == 1]
    df = df[(df["retentionTime"] >= rt_lower) & (df["retentionTime"] <= rt_higher)]
    ms1_scans_tol = list(df["num"])
    return ms1_scans_tol



def getPrecursorPeak(dfMz, surveyScanNumber,nominalPrecMz,  rt_higher, rt_lower, ms1_tol):
    # Find the precursor peak of the PSM (the strongest peak within the isolation window)
    #get ms1 informatio
    
    df = dfMz.loc[dfMz.num == str(surveyScanNumber)]
    
    df = df[(df["retentionTime"].astype("float") >= rt_lower) & (df["retentionTime"].astype("float") <= rt_higher)]
    
    
    mzArray = np.array(df["m/z array"].to_list()[0])
    intArray = np.array(df["intensity array"].to_list()[0])
    
    max_prec_mzCheck = nominalPrecMz+(nominalPrecMz/1000000*ms1_tol)
    min_prec_mzCheck = nominalPrecMz-(nominalPrecMz/1000000*ms1_tol)
    
    ind = (mzArray >= min_prec_mzCheck) & (mzArray <= max_prec_mzCheck)
    if sum(ind > 0):
        subMzArray = mzArray[ind]
        subIntArray = intArray[ind]
        ind2 = np.argmax(subIntArray)
        precMz = subMzArray[ind2]
        precIntensity = subIntArray[ind2]
    else:
        precMz = -1
        precIntensity = -1
    precRt = df["retentionTime"].values[0]   # Unit of minute
    
    return precMz, precIntensity, precRt




def get_rt(psms, mzxml): #psms = df of idtxt
    
    ms2ToSurvey,rt_dict,all_ms1_scans, dfMz = getMs2ToSurvey(mzxml)
    
    mzXMLBaseName = os.path.basename(mzxml).split(".")[0]
    psms_run = psms.loc[psms["Outfile"].str.contains(mzXMLBaseName+"\.")]    # Extract PSMs from FTLD_Batch2_F50.mzXML
    
    keys = list(set(psms_run["keys"]))
    
    # list of outfiles
    proton = mass.calculate_mass(formula='H+') #proron mono mass

    psm_scan_list = []
    prec_mz_list = []
    prec_int_list = []
    rt_list = []
    keys_list = []
    
    mz_cols = list(psms_run.columns)
    
    
    prec_key_cnt = 0
    for key in keys:
        prec_key_cnt +=1 
        
        if prec_key_cnt%1000 == 0:
            print ("Total precursors {} analyzed out of {}".format(prec_key_cnt,len(keys)))
        
        psms_subset = psms_run.loc[psms_run["keys"]==key]
        pep, z = key.split("_")
        
        np_arr = psms_subset.to_numpy()
        for row in np_arr:
            
            keys_list.append(key)
            outfiles = str(row[mz_cols.index("Outfile")])
            [_, psmScanNum, _, _, _] = os.path.basename(outfiles).split(".")
            
            
            precRt = rt_dict[int(psmScanNum)]
            
            df = dfMz[dfMz["num"]==psmScanNum]
            
            
            intArray = np.array(df["intensity array"].to_list()[0])
            prec_int = np.max(intArray)
            
            measuredMH = row[mz_cols.index("measuredMH")]
            nominalPrecMz = ((measuredMH - proton)+(int(z)*proton))/int(z)

                    
            psm_scan_list.append(psmScanNum)
            rt_list.append(precRt)
            prec_mz_list.append(nominalPrecMz)
            prec_int_list.append(prec_int)
            

#             print ("total MS1 checked = {}".format(cnt))
    out_table = pd.DataFrame({"peptide_charge":keys_list,"ms2_scan":psm_scan_list,"prec_mz":prec_mz_list,"prec_intensity":prec_int_list,"ms2_rt":rt_list})
    
    return out_table


#eps = Epsilon parameter of DBSCAN, It is the furthest distance at which a point will pick its neighbours
#points are list : for example RT values in the list format
# eps = RT difference in minutes
def clusteringSliding(points, eps=1):
    clusters = []
    points_sorted = sorted(points)
    curr_point = points_sorted[0]
    curr_cluster = [curr_point]
    for point in points_sorted[1:]:
        if point <= curr_point + eps:
            curr_cluster.append(point)
        else:
            clusters.append(curr_cluster)
            curr_cluster = [point]
        curr_point = point
    clusters.append(curr_cluster)
    return clusters






def select_singleton_cluster(row):
    max_int_rt_dict = row.max_int_rt_dict
    if len(max_int_rt_dict.keys()) == 1:
        #found the singleton cluster
        return list(max_int_rt_dict.values())[0]
    else:
        return -1





def select_first_cluster(row, eps):
    rt_clusters = row["RT_peaks_final_eps{}".format(eps)]
    if rt_clusters != -1:
        return rt_clusters[0]
    else:
        return rt_clusters




def rt_non_tailed_multicluster(row):
    rt_known = row["final_RT_multipsm_multicluster"]
    int_rt_dict = row["max_int_rt_dict"]
    if rt_known == -1:
        max_int = np.max(list(int_rt_dict.keys()))
        rt = int_rt_dict[max_int]
        return rt
    else:
        return rt_known




def extractRT(out_table, eps):
    res_f1 = out_table.groupby("peptide_charge").agg(list)
    res_f1["nPSMs"] = res_f1.apply(lambda x: len(x.ms2_rt), axis=1)
    res_f1["RT_Clust_eps_{}".format(eps)]=res_f1.apply(lambda x: clusteringSliding(x.ms2_rt, eps=2), axis=1)
    res_f1["rt_int_dict"] = res_f1.apply(lambda x: int_rt_dict(x.ms2_rt, x.prec_intensity), axis=1)
    '''
    select singleton cluster
    1. check the length of RT_Clust_eps_2 (for eps = 2) and see if the len of list of list is 1
    2. if so extract the intensity of the list of list using rt_int_dict
    3. Assign maximum intensity_rt as the dictionary for the cluster
    4. Select the dictionary that has single key
    4. Assign that RT

    '''
    res_f1["max_int_rt_dict"] = res_f1.apply(lambda x: getMaxIntCluster(x["RT_Clust_eps_{}".format(eps)], x.rt_int_dict), axis=1)
    
    #these are best RTs and as tehre is only one option

    res_f1["weighted_rt_list"] = res_f1.apply(lambda x: weighted_average_each_cluster(x["RT_Clust_eps_{}".format(eps)], x.rt_int_dict), axis=1)
    
    #res_f1["final_RT_singleton"] = res_f1.apply(select_singleton_cluster, axis=1) #max intensity based RT
    res_f1["final_RT_singleton"] = res_f1.apply(select_singleton_cluster_wtrt, axis=1) #weighted RT
    '''
    This approach helped 98% to be resolved

    CASE1 all final_RT_singleton == -1 are multiclusters

    3 more cases of multiclusters
    CASE2 = multipeak clusters [MC] --> The clusters have multiple peaks
    CASE4 = single peak clusters/ sigleton cluster [SC]
    CASE3 = [MC]+[SC]


    #get Case 2, 3, 4
    #get multipeak cluster (Case 2)


    '''
    #use filter to remove singleton peaks for multiple occurrence
    res_f1[["RT_peaks_evaluate_eps{}".format(eps),"clusterType"]] = res_f1.apply(evalute_rt_cluster,column="RT_Clust_eps_{}".format(eps), axis=1)
    #idea is to use earlier LC profile for tailed peaks

    # res_f1["final_RT_case2"] = res_f1.apply(inferRT_Case2, clusterExplore = "Case2", column1 = "final_RT_singleton", column2 ="RT_peaks_evaluate_eps{}".format(eps) , axis=1) #max intensity
    res_f1["final_RT_case2"] = res_f1.apply(inferRT_Case2_wtrt, clusterExplore = "Case2", column1 = "final_RT_singleton", column2 ="RT_peaks_evaluate_eps{}".format(eps) , axis=1) #weighted rt


    #multiple singleton cluster
    res_f1["final_RT_case4"] = res_f1.apply(inferRT_Case4, clusterExplore = "Case4", column1 = "final_RT_case2", column2 ="RT_peaks_evaluate_eps{}".format(eps) , axis=1)
    
    #use filter to remove singleton peaks for multiple occurrence could be subcase2 and subcase4
    res_f1["subClusterTypeCase3"] = res_f1.apply(evalute_rt_cluster_case3,column="RT_peaks_evaluate_eps{}".format(eps), axis=1)
   
    # res_f1["final_RT_case3_subcase2"]=res_f1.apply(inferRT_case3_subcase2, eps = eps, axis=1) #RT based on max intensity
    res_f1["final_RT_case3_subcase2"]=res_f1.apply(inferRT_case3_subcase2_wtrt, eps = eps, axis=1) #weighted RT

    res_f1["Final_RT"]=res_f1.apply(inferRT_case3_subcase1, eps = eps, axis=1) # same as final_RT_case3_subcase1 and all others


    return res_f1







def formatRtTable2(df, runs):
    df_nPSMs = df.set_index(['key', 'run']).nPSMs.unstack().reset_index()
    df_RT = df.set_index(['key', 'run']).RT.unstack().reset_index()
#     df_RT = df.set_index(['key', 'run']).pseudoRT.unstack().reset_index()

    df_nPSMs2 = df_nPSMs.set_index("key")
    df_RT2 = df_RT.set_index("key")
    #apply runs columns to maintain the order. Unstack sort the columns based on string and 1, 10, 100 and so on we need correct order
    df_RT2 = df_RT2[runs]
    col_keys_nPSM ={}
    for val in runs:
        new_val = val+"_nPSMs"
        col_keys_nPSM[val]=new_val

    df_nPSMs3 = df_nPSMs2.rename(columns=col_keys_nPSM)
    df_nPSMs3 = df_nPSMs3[list(col_keys_nPSM.values())]
    
    res = pd.concat([df_RT2,df_nPSMs3], axis=1)
    res.reset_index(inplace=True)
    return res





def alignRT(df, runs, tol_min=1):
    # Input: df = a pandas dataframe containing keys (peptide-charge pairs) and RTs over the runs (i.e., mzXML files)
    #        runs = the list of mzXML runnames in order of needed alignment
    # tol_min = tolerance of RT for deciding the consensus RT
    
    '''
    We will get 5 populations of peptides (shared <2 mins, shared >= 2 mins, reference unique, target unique, not existing in ref and target)
    For shared peptides, Use weighted averaged RT based on PSMs (ref has original RT and target has calibrated RT), record the sum PSM and SD of the RT (Pop1)
    For shared peptids Pop2, (>= 2minutes), select max psms and retain rt, if psms# is same retain RT from reference
    For reference unique peptides, keep the original reference inferred RT
    For target unique peptides, keep the calibrated RT
    If the peptide does not exist in current ref or target, the values are na
    Generate a new reference (concatenating all populations of peptides along with their aligned RT)'''
    
    print("  Alignment and calibration of RTs over {} runs".format(len(runs)))
    print("  ==============================================\n")
    print("  {} run is selected as the reference fraction".format(runs[0]))
    
    tol_min_bk = tol_min  # this is for backup tolerance in case some fractions alignment was not done with the given tolerance. We retrieve this tolerance later in the loop
    
    res = df.copy()
    keys = res["key"]
#     runs = ["FTLD_Batch2_F64","FTLD_Batch2_F65","FTLD_Batch2_F66"]
    run_n_psm = [x+"_nPSMs" for x in runs]
    
    #initializing the loess function in R
    rLoess = loess()
    rPredict = ro.r("predict")
    
    deltaRT_recorder = []
    print ("    The reference run is now being aligned with target runs")

    progress = progressBar(len(runs))
    
    # testpep  = "K.VMEPILQILQQK.F_2"
    
    
    for exp in range(1,len(runs)):
        tol_min = tol_min_bk
        progress.increment()
       

        ref = res[runs[0]] #this is always the reference run and it is updated
        target = res[runs[exp]] #this keeps changing with each loop based on given run list

        idx = (~ref.isna()) & (~target.isna()) #this is finding the shared peptide using the true false index
        x = ref[idx] #shared peptide from reference
        y = target[idx] #shared peptide from target

        keys_model = keys[idx] # this is the keys used for modeling loess

        idx_ = (~keys_model.str.contains("M@")) #removes M@ peptides from modeling. this is important as they have multiple peaks 
        xmod = x[idx_]
        ymod = y[idx_]


        # Build a LOESS model and calibrate RTs
        mod = rLoess(FloatVector(ymod), FloatVector(xmod))  # LOESS model based on the shared peptides
        cal_target = rPredict(mod, FloatVector(target))  # Calibration is applied to whole peptides of the current run



        # shared peptides after calibration .. this will be used to create the calibrated column in dataframe
        y_ = np.array(cal_target)[idx]
        
        #replace target runs with calibrated RT
        res[runs[exp]] = cal_target
        #selection of consensus RT
        delRT = x - y_ # difference between the reference run and calibrated target run
        
        delRT_R_T = pd.DataFrame({"key":keys_model, "{}-{}".format(runs[0],runs[exp]):delRT})
        deltaRT_recorder.append(delRT_R_T)
        
        id_delRT_p1 = abs(delRT) < tol_min #this is a parameter tolerance in minute (could be 1 minute or 2 minute) 
        #keys that have population 1 as described above
        keys_pop1 = keys_model[id_delRT_p1]
        
        while keys_pop1.shape[0] == 0:
            tol_min+=1
            id_delRT_p1 = abs(delRT) < tol_min #this is a parameter tolerance in minute (could be 1 minute or 2 minute) 
            #keys that have population 1 as described above
            keys_pop1 = keys_model[id_delRT_p1]
        
#         print (keys_pop1.shape[0])

        #dataframe with population 1
        df_pop1 = res[res["key"].isin(keys_pop1)]
        #weighted RT based on psms and inferred RT for population 1
        df_pop1[runs[0]]=df_pop1.apply(weighted_average2, runs=runs, exp=exp, run_n_psm=run_n_psm, axis=1)
        
        #takes the mean of psms for those runs that have values ... ignore nan
        rt_nPSM1 = np.nanmean(df_pop1[[run_n_psm[0], run_n_psm[exp]]].values,axis=1)
        df_pop1[run_n_psm[0]] = rt_nPSM1
        
        #take maximum psms
#         rt_nPSM1 = np.max(df_pop1[[run_n_psm[0], run_n_psm[exp]]].values,axis=1)
        
        #add population type
        df_pop1["Type_RT{}".format(runs[exp])] = "Pop1" # this is just for record which population type was peptide for each alignment
        


        id_delRT_p2 = abs(delRT) >= tol_min #Population 2 criteria
        #keys that have pop2
        keys_pop2 = keys_model[id_delRT_p2] #peptides for population 2

        df_pop2 = res[res["key"].isin(keys_pop2)] #extract dataframe only for population 2

        #applies functions pop2_rt_consensus that select the consensus RT based on higher PSMs # and if there is a tie selects reference RT
        df_pop2[[runs[0],run_n_psm[0]]] = df_pop2.apply(pop2_rt_consensus, runs=runs, exp=exp, run_n_psm=run_n_psm, axis=1) 
        df_pop2["Type_RT{}".format(runs[exp])] = "Pop2" #specifies which population of peptide it is for the record
        

        
        #pop3 reference only
        idx_pop3 = (~ref.isna()) & (target.isna()) #the index of peptides that are only present in reference

        #extract df that has unique reference peptides
        df_pop3 = res[idx_pop3]
        df_pop3["Type_RT{}".format(runs[exp])] = "Pop3" #specifies which population of peptide it is for the record

        #pop4 target only
        idx_pop4 = (ref.isna()) & (~target.isna()) #the index of peptides that are only present in target

        #extract df that has unique target peptides
        df_pop4 = res[idx_pop4]  
        
        df_pop4[runs[0]] = df_pop4[runs[exp]] #assigns the RT values for unique targets to the reference run[0] is reference from mzxmls list
        df_pop4[run_n_psm[0]] = df_pop4[run_n_psm[exp]] #assigns the psms for unique targets to the reference run
        df_pop4["Type_RT{}".format(runs[exp])] = "Pop4" #specifies which population of peptide it is for the record

        
        #population 5 has no peptide in reference and aligned run we need to keep this to make the loop going
        idx_pop5 = (ref.isna()) & (target.isna()) 
        #extract df that do not have reference and target peptides
        df_pop5 = res[idx_pop5] #dataframe for peptides missed in reference and target
        df_pop5["Type_RT{}".format(runs[exp])] = "Pop5" #specifies which population of peptide it is for the record

        
        #all dataframes are concatenated as super datframe
        super_ref_df = df_pop1.append(df_pop2.append(df_pop3.append(df_pop4.append(df_pop5)))) 
        
        #the starting dataframe res is now redefined using the copy of super dataframe
        res = super_ref_df.copy()
        
#         print ("ROUND {}".format(exp))
        
#         print ("pop1\t{}".format(df_pop1[df_pop1["key"]==testpep][runs[0]].values))
#         print ("pop2\t{}".format(df_pop2[df_pop2["key"]==testpep][runs[0]].values))
#         print ("pop3\t{}".format(df_pop3[df_pop3["key"]==testpep][runs[0]].values))
#         print ("pop4\t{}".format(df_pop4[df_pop4["key"]==testpep][runs[0]].values))
#         print ("pop5\t{}".format(df_pop5[df_pop5["key"]==testpep][runs[0]].values))
        
#         print ("pop1\t{}".format(df_pop1[df_pop1["key"]==testpep][run_n_psm[0]].values))
#         print ("pop2\t{}".format(df_pop2[df_pop2["key"]==testpep][run_n_psm[0]].values))
#         print ("pop3\t{}".format(df_pop3[df_pop3["key"]==testpep][run_n_psm[0]].values))
#         print ("pop4\t{}".format(df_pop4[df_pop4["key"]==testpep][run_n_psm[0]].values))
#         print ("pop5\t{}".format(df_pop5[df_pop5["key"]==testpep][run_n_psm[0]].values))

    print("  Done ...\n")
    
    return res,deltaRT_recorder



def alignRT_OLD(df, runs, tol_min=1):
    # Input: df = a pandas dataframe containing keys (peptide-charge pairs) and RTs over the runs (i.e., mzXML files)
    #        runs = the list of mzXML runnames in order of needed alignment
    # tol_min = tolerance of RT for deciding the consensus RT
    
    '''
    We will get 5 populations of peptides (shared <2 mins, shared >= 2 mins, reference unique, target unique, not existing in ref and target)
    For shared peptides, Use weighted averaged RT based on PSMs (ref has original RT and target has calibrated RT), record the sum PSM and SD of the RT (Pop1)
    For shared peptids Pop2, (>= 2minutes), select max psms and retain rt, if psms# is same retain RT from reference
    For reference unique peptides, keep the original reference inferred RT
    For target unique peptides, keep the calibrated RT
    If the peptide does not exist in current ref or target, the values are na
    Generate a new reference (concatenating all populations of peptides along with their aligned RT)'''
    
    print("  Alignment and calibration of RTs over {} runs".format(len(runs)))
    print("  ==============================================\n")
    print("  {} run is selected as the reference fraction".format(runs[0]))
    
    res = df.copy()
    keys = res["key"]
#     runs = ["FTLD_Batch2_F64","FTLD_Batch2_F65","FTLD_Batch2_F66"]
    run_n_psm = [x+"_nPSMs" for x in runs]
    
    #initializing the loess function in R
    rLoess = loess()
    rPredict = ro.r("predict")
    
    deltaRT_recorder = []
    print ("    The reference run is now being aligned with target runs")

    progress = progressBar(len(runs))
    
    for exp in range(1,len(runs)):
        progress.increment()
       

        ref = res[runs[0]] #this is always the reference run and it is updated
        target = res[runs[exp]] #this keeps changing with each loop based on given run list

        idx = (~ref.isna()) & (~target.isna()) #this is finding the shared peptide using the true false index
        x = ref[idx] #shared peptide from reference
        y = target[idx] #shared peptide from target

        keys_model = keys[idx] # this is the keys used for modeling loess

        # Build a LOESS model and calibrate RTs
        mod = rLoess(FloatVector(x), FloatVector(y))  # LOESS model based on the shared peptides
        cal_target = rPredict(mod, FloatVector(target))  # Calibration is applied to whole peptides of the current run

    #     print (np.array(cal_target)[idx])

        # shared peptides after calibration .. this will be used to create the calibrated column in dataframe
        y_ = np.array(cal_target)[idx]


        #selection of consensus RT
        delRT = x - y_ # difference between the reference run and calibrated target run
        
        delRT_R_T = pd.DataFrame({"key":keys_model, "{}-{}".format(runs[0],runs[exp]):delRT})
        deltaRT_recorder.append(delRT_R_T)
        
        id_delRT_p1 = abs(delRT) < tol_min #this is a parameter tolerance in minute (could be 1 minute or 2 minute) 
        #keys that have population 1 as described above
        keys_pop1 = keys_model[id_delRT_p1]

        #dataframe with population 1
        df_pop1 = res[res["key"].isin(keys_pop1)]
        #weighted RT based on psms and inferred RT for population 1
        # rt_pop1 = weighted_average(df_pop1, runs, run_n_psm)
        rt_pop1 = weighted_average(df_pop1, [runs[0], runs[exp]], [run_n_psm[0], run_n_psm[exp]])

        #takes the mean of psms for those runs that have values ... ignore nan
        rt_nPSM1 = np.nanmean(df_pop1[run_n_psm].values,axis=1)
        
        pop1_key_rt_dict = dict(zip(keys_pop1,rt_pop1)) #dictionary for peptides with their RT
        pop1_key_psm_dict = dict(zip(keys_pop1,rt_nPSM1)) #dictionary for peptides with their PSMs

        df_pop1[runs[0]] = df_pop1["key"].map(pop1_key_rt_dict) #map back the aligned RT (weighted) replacing original RT ;; this keeps on updating
        df_pop1[run_n_psm[0]] = df_pop1["key"].map(pop1_key_psm_dict) #map back mean psms replacing original psm ;; this keeps on updating
        #add population type
        df_pop1["Type_RT{}".format(runs[exp])] = "Pop1" # this is just for record which population type was peptide for each alignment


        id_delRT_p2 = abs(delRT) >= tol_min #Population 2 criteria
        #keys that have pop2
        keys_pop2 = keys_model[id_delRT_p2] #peptides for population 2

        df_pop2 = res[res["key"].isin(keys_pop2)] #extract dataframe only for population 2

        #applies functions pop2_rt_consensus that select the consensus RT based on higher PSMs # and if there is a tie selects reference RT
        df_pop2[[runs[0],run_n_psm[0]]] = df_pop2.apply(pop2_rt_consensus, runs=runs, exp=exp, run_n_psm=run_n_psm, axis=1) 
        df_pop2["Type_RT{}".format(runs[exp])] = "Pop2" #specifies which population of peptide it is for the record

        #pop3 reference only
        idx_pop3 = (~ref.isna()) & (target.isna()) #the index of peptides that are only present in reference

        #extract df that has unique reference peptides
        df_pop3 = res[idx_pop3]
        df_pop3["Type_RT{}".format(runs[exp])] = "Pop3" #specifies which population of peptide it is for the record


        #pop4 target only
        idx_pop4 = (ref.isna()) & (~target.isna()) #the index of peptides that are only present in target

        #extract df that has unique target peptides
        df_pop4 = res[idx_pop4]  

        df_pop4[runs[0]] = df_pop4[runs[exp]] #assigns the RT values for unique targets to the reference run[0] is reference from mzxmls list
        df_pop4[run_n_psm[0]] = df_pop4[run_n_psm[exp]] #assigns the psms for unique targets to the reference run
        df_pop4["Type_RT{}".format(runs[exp])] = "Pop4" #specifies which population of peptide it is for the record

        #population 5 has no peptide in reference and aligned run we need to keep this to make the loop going
        idx_pop5 = (ref.isna()) & (target.isna()) 
        #extract df that do not have reference and target peptides
        df_pop5 = res[idx_pop5] #dataframe for peptides missed in reference and target
        df_pop5["Type_RT{}".format(runs[exp])] = "Pop5" #specifies which population of peptide it is for the record
        
        #all dataframes are concatenated as super datframe
        super_ref_df = df_pop1.append(df_pop2.append(df_pop3.append(df_pop4.append(df_pop5)))) 
        
        #the starting dataframe res is now redefined using the copy of super dataframe
        res = super_ref_df.copy()
        

        # if cnt%10 == 0:
        #     print ("    Alignment completed for refernce and  {} target runs".format(cnt))

        
    # Organization of the output dataframe
#     # Calculation of the weighted standard deviation of RTs (https://www.itl.nist.gov/div898/software/dataplot/refman2/ch2/weightsd.pdf)
#     M = (~res[run_n_psm].isna()).sum(axis=1)
#     den = ((M - 1) / M) * res[run_n_psm].sum(axis=1)
#     num = ((res[runs].sub(ref, axis=0) ** 2) * res[run_n_psm].values).sum(axis=1)
#     sdRt = np.sqrt(num / den)
#     sdRt[den == 0] = 0
#     res["SdRT"] = sdRt
#     # Calculation of the weighted average RTs
#     res["AvgRT"] = ref  # In fact, the final reference is equivalent to the weighted average of the aligned/calibrated RTs

    print("  Done ...\n")
    
    return res,deltaRT_recorder





def inferRT(idtxt, runs, eps):
    # Input
    # 1. mzXML files
    # 2. ID.txt file containing all identified PSMs
    print("  Extraction and assignment of RTs to the identified PSMs")
    print("  =======================================================")

    # Read ID.txt files to extract PSM information
    print("  Read ID.txt file: to extract PSM information")
    psms = parse_idtxt(idtxt)
    
    # RT extraction/assignment for each mzXML file
    key_list = []
    run_list = []
    rt_list = []
    npsm_list = []
    pseudo_rt_list = []

    ext_data_dict = {} # this dictionary has runwise dataframes 

    runName_list = []

#     print (runs)
    for run in runs:
        
        runName = os.path.basename(run).split(".")[0]
        runName_list.append(runName)
        print("  Working now on extracting RTs from {}.\n".format(run))
        
        
        print("  RT of every identified peptide in {} is being inferred and assigned".format(runName))
        out_table = get_rt(psms, run)
        res_f1 = extractRT(out_table, eps)
        
        #update dictionary
        ext_data_dict[runName] = res_f1
        
        key_list.extend(list(res_f1.index))
        run_list.extend([runName]*res_f1.shape[0])
        rt_list.extend(list(res_f1.Final_RT))

        #makes a pseudo RT list for each file run .... the total run time is extracted by get_run_len
#         pseudo_rt_eachfile = np.array(list(res_f1.Final_RT))/get_run_len(run)*100 #normalizes all runs to 100 RT 
#         pseudo_rt_list.extend(pseudo_rt_eachfile)

        npsm_list.extend(list(res_f1.nPSMs))
        
        print("  Completed extracting RTs from {}.\n".format(run))

    
#     res = pd.DataFrame({"key":key_list,"run":run_list,"RT":rt_list,"pseudoRT":pseudo_rt_list,"nPSMs":npsm_list})
    res = pd.DataFrame({"key":key_list,"run":run_list,"RT":rt_list,"nPSMs":npsm_list})
    
    res = formatRtTable2(res, runName_list) #Previous formatRtTable function is replaced by formatRtTable2. This fucntion is very quick compared to previous one
    
    return ext_data_dict,res



def getOrderedMzxmlList(mzxml_path, orderedFraction): #orderedFraction = file that contians ordered fractions with one fraction in one row. 
    #this updates the new mzXML list based on the ordered fraction list provided
    df = pd.read_csv(orderedFraction, delimiter="\t", header=None)
    df["mzXML"] = mzxml_path+"/"+df[0]+".mzXML"
    mzXML_list = list(df["mzXML"])
    return mzXML_list 



###### FUnction to find the total run lenght of mzXML file ####

def get_run_len(mzxml):
    f = open(mzxml,"r") #read the file
    line = f.readline()
    var_AA_mass = {} 
    var_AA_symbol = {} #symbol mass dictionary
    stat_AA_mass = {}
    while "<msRun scanCount=" not in line: #end reading the file if this is sen
        line = f.readline()

    #     #<msRun scanCount="35023" startTime="PT480.156S" endTime="PT5400.19S" >
    pattern = 'endTime="PT(\d+(\.\d+)?)S"'
    
    m= re.search(pattern, line)
#     print (mzxml,float(m[1])/60)
    
    return float(m[1])/60 # time in minutes



#################################SUMMARY###########################



def summary(filename,df,delRT_Col = "delRT"): # df is alignment with 2 fractions only
    
    overlapped_prec_list = []
    rt_tolerance_list = []
    percentage_prec_list = []
    
    with open(filename,"w") as f:
        f.write("Overlapped Precursor\tdelta RT (tol)\tPercentage (%) precursors\n")
        print ("Overlapped Precursor\tdelta RT (tol)\tPercentage (%) precursors")
        for x in np.arange(0.5, 10, 0.5):
            cnt = df.loc[abs(df[delRT_Col]) < x].shape[0]
            f.write("{}\t{}\t{}\n".format(cnt, x, cnt/df.shape[0]*100))
            print ("{}\t{}\t{}".format(cnt, x, cnt/df.shape[0]*100))
            overlapped_prec_list.append(cnt)
            rt_tolerance_list.append(x)
            percentage_prec_list.append(cnt/df.shape[0]*100)
    newDF = pd.DataFrame({"Overlap_Prec":overlapped_prec_list,"rt_tolerance":rt_tolerance_list,"overlap_prec_percentage":percentage_prec_list})
    
    return newDF

'''
# Suresh method infered RT file check delRT before alignment
df_pkl = "/home/spoudel1/spectral_library_manuscript/extract_RT/program_ms2_based_RT/serum_samples/all_fractions_RT.pkl"
df_suresh_infer = pd.read_pickle(df_pkl)

df_suresh_infer["delRT_before"] = df_suresh_infer.Yadav_B10 - df_suresh_infer.Yadav_B11 

#remove na Suresh inference
df_suresh_infer_noNA = df_suresh_infer.dropna()
suresh_before = summary("suresh_inference_before_alignment",df_suresh_infer_noNA,delRT_Col = "delRT_before")


'''


################### FUnctions for consensus RT selection after alignment ###################



def pop2_rt_consensus(row, runs,exp, run_n_psm):
    keys = row["key"]
    ref =  float(row[runs[0]])
    target =  float(row[runs[exp]])
    
    refPSM =  float(row[run_n_psm[0]])
    targetPSM =  float(row[run_n_psm[exp]])
    
    if (refPSM > targetPSM) | (refPSM == targetPSM):
        return pd.Series([ref, refPSM])
#         final_rt.append(ref)
#         final_psms.append(refPSM)
    else: 
        return pd.Series([target, targetPSM])

#         final_rt.append(target)
#         final_psms.append(targetPSM)
    
#     return pd.Series([final_rt, final_psms])


def weighted_average2(row,runs,exp,run_n_psm):
    ref =  float(row[runs[0]])
    target =  float(row[runs[exp]])
    
    refPSM =  float(row[run_n_psm[0]])
    targetPSM =  float(row[run_n_psm[exp]])
    
    refVal = ref*refPSM
    tarVal = target*targetPSM
    
    num = refVal+tarVal
    den = refPSM+targetPSM
    
    weightedRT = num/den
    
    return weightedRT


def weighted_average(df, rt_cols_list, rt_weights_cols_list):
    #numpy array of rt values for 2 columns rt_cols_list
    rt_values = df[rt_cols_list].values
    #numpy array of rt weights here psms for 2 columns rt_weights_cols_list

    rt_weights = df[rt_weights_cols_list].values
    #ignore nan when summing across the axis =1 (row)
    weighted_rt = np.nansum(rt_values*rt_weights, axis=1)/np.nansum(rt_weights, axis=1)
#     weighted_rt = (df[rt_cols_list].values*df[rt_weights_cols_list].values).sum(axis=1)/df[rt_weights_cols_list].values.sum(axis=1)
    return weighted_rt
