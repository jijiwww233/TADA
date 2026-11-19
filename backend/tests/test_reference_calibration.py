from PIL import Image,ImageDraw
import numpy as np
from app.reference_calibration import cheap_features,decide,prompt_scores,PROMPT_GROUPS


def test_uniform_background_and_text_density_are_measured_locally(tmp_path):
    blank=tmp_path/'blank.png';text=tmp_path/'text.png'
    image=Image.new('RGB',(220,300),'white');image.save(blank)
    draw=ImageDraw.Draw(image)
    for y in range(20,280,15):draw.text((12,y),'Some written text 123456',fill='black')
    image.save(text)
    a,b=cheap_features(blank),cheap_features(text)
    assert a[3]==1 and a[9]==0 and b[9]>0 and b[3]<a[3]
    assert all(np.isfinite(b))


def test_prompt_ensemble_distinguishes_text_and_retains_low_margin_as_uncertain():
    vectors=np.array([[1,0,0]]*5+[[0,1,0]]*5+[[0,0,1]]*3)
    score=prompt_scores(np.array([[0,1,0],[.5,.5,0]]),vectors)
    assert decide(score[0],[],[],None)[0]=='text_card'
    assert decide(score[1],[],[],None)[0]=='uncertain'


def test_profile_invalid_dimensions_fail_to_uncertain_instead_of_exclusion():
    profile={'mean':[0], 'scale':[1], 'coefficients':[1]}
    assert decide([.3,.2,.1],[0]*13,[0]*512,profile)[0]=='uncertain'
