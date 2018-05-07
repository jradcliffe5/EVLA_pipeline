__rethrow_casa_exceptions = True
context = h_init()
#context = h_resume()
context.set_state('ProjectSummary', 'proposal_code', 'VLA/TSKY0001')
context.set_state('ProjectSummary', 'observatory', 'Karl G. Jansky Very Large Array')
context.set_state('ProjectSummary', 'telescope', 'EVLA')
context.set_state('ProjectSummary', 'piname', 'unknown')
context.set_state('ProjectSummary', 'proposal_title', 'unknown')

measurement_set = ''

try:
    hifv_importdata(vis=[measurement_set], session=['session_1'])
    hifv_hanning(pipelinemode="automatic")
    hifv_flagdata(intents='*POINTING*,*FOCUS*,*ATMOSPHERE*,*SIDEBAND_RATIO*, *UNKNOWN*, *SYSTEM_CONFIGURATION*, *UNSPECIFIED#UNSPECIFIED*', hm_tbuff='manual', tbuff=0.225, fracspw=0.05, quack=False)
    hifv_vlasetjy(pipelinemode="automatic")
    hifv_priorcals(swpow_spw='6,14', tecmaps=False)
    hifv_testBPdcals(refantignore='ea24')
    hifv_flagbaddef(pipelinemode="automatic")
    hifv_checkflag(checkflagmode='bpd')
    hifv_semiFinalBPdcals(refantignore='ea24')
    hifv_checkflag(checkflagmode='allcals')
    hifv_solint(refantignore='ea24', limit_short_solint='0.45')
    hifv_fluxboot2(refantignore='ea24')
    hifv_finalcals(refantignore='ea24')
    hifv_circfeedpolcal(pipelinemode="automatic")
    hifv_flagcal(pipelinemode="automatic")
    hifv_applycals(flagdetailedsum=False, flagsum=False, gainmap=True)
    hifv_checkflag(checkflagmode='target')
    hifv_statwt(pipelinemode="automatic")
    hifv_plotsummary(pipelinemode="automatic")
finally:
    h_save()
