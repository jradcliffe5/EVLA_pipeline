__rethrow_casa_exceptions = True
context = h_init()
#context = h_resume()
context.set_state('ProjectSummary', 'proposal_code', 'VLA/18A-342')
context.set_state('ProjectSummary', 'observatory', 'Karl G. Jansky Very Large Array')
context.set_state('ProjectSummary', 'telescope', 'EVLA')
context.set_state('ProjectSummary', 'piname', 'unknown')
context.set_state('ProjectSummary', 'proposal_title', 'unknown')
import os
measurement_set = '18A-392.sb35213326.eb35411259.58253.14186105324'

try:
    os.system('tar -xvf %s.tar' % measurement_set)
    hifv_importdata(vis=[measurement_set], session=['session_1'])
    hifv_hanning(pipelinemode="automatic")
    hifv_flagdata(intents='*POINTING*,*FOCUS*,*ATMOSPHERE*,*SIDEBAND_RATIO*, *UNKNOWN*, *SYSTEM_CONFIGURATION*, *UNSPECIFIED#UNSPECIFIED*', hm_tbuff='manual', tbuff=0.225, fracspw=0.05, quack=False)
    hifv_vlasetjy(pipelinemode="automatic")
    hifv_priorcals(swpow_spw='', tecmaps=False)
    hifv_testBPdcals(refantignore='')
    hifv_flagbaddef(pipelinemode="automatic")
    hifv_checkflag(checkflagmode='bpd')
    hifv_semiFinalBPdcals(refantignore='')
    hifv_checkflag(checkflagmode='allcals')
    hifv_solint(refantignore='', limit_short_solint='0.45')
    hifv_fluxboot2(refantignore='')
    hifv_finalcals(refantignore='')
    hifv_circfeedpolcal(pipelinemode="automatic")
    hifv_flagcal(pipelinemode="automatic")
    hifv_applycals(flagdetailedsum=False, flagsum=False, gainmap=True)
    hifv_checkflag(checkflagmode='target')
    hifv_statwt(pipelinemode="automatic")
    hifv_plotsummary(pipelinemode="automatic")
finally:
    h_save()
